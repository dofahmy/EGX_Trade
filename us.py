import re
"""
جلب صفقات الأسهم الأمريكية (إس آند بي ٥٠٠) من داتابينتو، وتحويلها لنفس شكل ملف "تنفيذات الجلسة"
عشان نفس الحسابات تشتغل عليها.

- مصدر البيانات: بورصة ناسداك (XNAS.ITCH)، لأنها بتدّي مين اللي بدأ كل صفقة (البايع ولا المشتري).
  يعني بنشوف تداول ناسداك بس، مش كل التداول على السهم.
- الجلسة: من ٩:٣٠ الصبح لـ٤ العصر بتوقيت نيويورك (٧٨ فترة خمس دقايق).
"""
import os, csv, datetime as dt
from pathlib import Path

import pandas as pd

from engine import day_stats, levels, bars, profile

BASE = Path(__file__).resolve().parent
DATASET = os.environ.get('US_DATASET', 'XNAS.ITCH')
TZ = 'America/New_York'
BATCH = int(os.environ.get('US_BATCH', '40'))



def _friendly(e):
    t = str(e)
    m = re.search(r'after (\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})', t)
    if 'license' in t.lower() and m:
        return ('بيانات الجلسة دي لسه ما اتاحتش في اشتراكك الحالي. داتابينتو بتفتح الجلسة بعد حوالي يوم من نهايتها، '
                'والمتاح دلوقتي لحد ' + m.group(1) + ' الساعة ' + m.group(2) + ' بتوقيت جرينتش. جربي تاني بكرة.')
    return t[:600]

def sp500():
    with open(BASE / 'sp500.csv', encoding='utf-8') as f:
        rows = list(csv.DictReader(f))
    return [(r['Symbol'].strip(), r['Security'].strip()) for r in rows if r.get('Symbol')]


def session_bounds(date: str):
    d = dt.date.fromisoformat(date)
    start = pd.Timestamp(dt.datetime.combine(d, dt.time(9, 30)), tz=TZ)
    end = pd.Timestamp(dt.datetime.combine(d, dt.time(16, 0)), tz=TZ)
    return start.tz_convert('UTC'), end.tz_convert('UTC')


def client():
    key = os.environ.get('DATABENTO_API_KEY', '').strip()
    if not key:
        raise RuntimeError('مفتاح داتابينتو مش متحط. ضيفي متغير DATABENTO_API_KEY في ريلواي.')
    import databento as db
    return db.Historical(key)


def estimate(date: str, symbols=None):
    """تكلفة جلب الجلسة بالدولار قبل التنزيل."""
    symbols = symbols or [s for s, _ in sp500()]
    start, end = session_bounds(date)
    c = client()
    cost = c.metadata.get_cost(dataset=DATASET, start=start, end=end, symbols=symbols, schema='trades')
    size = None
    try:
        size = c.metadata.get_billable_size(dataset=DATASET, start=start, end=end, symbols=symbols, schema='trades')
    except Exception:
        pass
    return float(cost), size


def to_trades(df: pd.DataFrame, names: dict) -> pd.DataFrame:
    """يحوّل جدول صفقات داتابينتو لنفس أعمدة ملف التنفيذات المصري."""
    if df is None or not len(df):
        return pd.DataFrame(columns=['sym', 'name', 'time', 'price', 'vol', 'chg', 'side', 'val', 'secs', 'seq'])
    ts = pd.to_datetime(df['ts_event'], utc=True).dt.tz_convert(TZ) if 'ts_event' in df else df.index.tz_convert(TZ)
    out = pd.DataFrame({
        'sym': df['symbol'].astype(str).values,
        'price': pd.to_numeric(df['price'], errors='coerce').values,
        'vol': pd.to_numeric(df['size'], errors='coerce').values,
        # A = البايع هو اللي بدأ الصفقة، B = المشتري، N = مش معروف
        'side': df['side'].astype(str).map({'A': 'S', 'B': 'B'}).fillna('N').values,
        'ts': ts.values if hasattr(ts, 'values') else ts,
    })
    out['ts'] = pd.to_datetime(out['ts'], utc=True).dt.tz_convert(TZ)
    out = out.dropna(subset=['price', 'vol'])
    out = out[(out['price'] > 0) & (out['vol'] > 0)]
    tod = out['ts'].dt.hour * 3600 + out['ts'].dt.minute * 60 + out['ts'].dt.second
    out = out[(tod >= 9 * 3600 + 30 * 60) & (tod < 16 * 3600)].copy()
    out['secs'] = (out['ts'].dt.hour * 3600 + out['ts'].dt.minute * 60 + out['ts'].dt.second).astype(int)
    out['time'] = out['ts'].dt.time
    out['name'] = out['sym'].map(names).fillna(out['sym'])
    out['val'] = out['price'] * out['vol']
    out['chg'] = float('nan')
    out = out.sort_values('ts', kind='stable').reset_index(drop=True)
    out['seq'] = range(1, len(out) + 1)
    return out.drop(columns=['ts'])


def fill_prev(store, date: str, st: pd.DataFrame) -> pd.DataFrame:
    """الإقفال السابق = آخر سعر في آخر جلسة متخزنة قبل التاريخ ده. ولو مفيش، نستخدم سعر الفتح."""
    before = [d for d in store.dates() if d < date]
    prev = None
    if before:
        prev = store.load(before[-1])[0]['last']
    st = st.copy()
    st['prev'] = st.index.map(lambda s: prev.get(s) if prev is not None and s in prev.index else None)
    st['prev'] = pd.to_numeric(st['prev'], errors='coerce').fillna(st['open'])
    st['chg'] = ((st['last'] / st['prev'] - 1) * 100).round(2)
    return st


def process_fetch(store, jid: str, date: str):
    try:
        names = dict(sp500())
        syms = list(names)
        start, end = session_bounds(date)
        c = client()
        stats, lvls, brs, prs, ntr = [], [], [], [], 0
        for i in range(0, len(syms), BATCH):
            batch = syms[i:i + BATCH]
            store.job_set(jid, state=f'بجيب الأسهم {i + 1}–{min(i + BATCH, len(syms))} من {len(syms)}')
            data = c.timeseries.get_range(dataset=DATASET, start=start, end=end, symbols=batch, schema='trades')
            df = data.to_df()
            if 'ts_event' not in df.columns:
                df = df.reset_index()
            tr = to_trades(df, names)
            if len(tr):
                ntr += len(tr)
                stats.append(day_stats(tr))
                lvls.append(levels(tr))
                brs.append(bars(tr))
                prs.append(profile(tr))
        if not stats:
            raise RuntimeError('مفيش صفقات في اليوم ده. ممكن يكون أجازة في البورصة الأمريكية، أو التاريخ لسه ماجاش.')
        st = pd.concat(stats)
        lv = pd.concat(lvls, ignore_index=True)
        st = fill_prev(store, date, st)
        meta = {'date': date, 'trades': int(ntr), 'stocks': int(len(st)), 'source': DATASET,
                'uploaded_at': dt.datetime.now().isoformat(timespec='seconds'), 'chain': []}
        store.save(date, st, lv, meta, pd.concat(brs, ignore_index=True), pd.concat(prs, ignore_index=True))
        # لو فيه جلسة بعدها متخزنة، نحدّث الإقفال السابق بتاعها
        after = [d for d in store.dates() if d > date]
        if after:
            s2, l2, m2 = store.load(after[0])
            store.save(after[0], fill_prev(store, after[0], s2), l2, m2)
        store.job_set(jid, state='تم', done=True, trades=int(ntr), stocks=int(len(st)), chain=[])
    except Exception as e:
        store.job_set(jid, state='حصل خطأ', done=True, error=_friendly(e))


def process_bars(store, jid: str, date: str, syms):
    """يجيب صفقات أسهم معينة بس في يوم قديم، ويكمّل بيها حركة اليوم دقيقة بدقيقة من غير ما يلمس الملخص."""
    try:
        names = dict(sp500())
        start, end = session_bounds(date)
        store.job_set(jid, state=f'بجيب حركة {len(syms)} سهم')
        df = client().timeseries.get_range(dataset=DATASET, start=start, end=end, symbols=list(syms), schema='trades').to_df()
        if 'ts_event' not in df.columns:
            df = df.reset_index()
        tr = to_trades(df, names)
        if not len(tr):
            raise RuntimeError('مفيش صفقات للأسهم دي في اليوم ده.')
        new = bars(tr)
        old = store.load_bars(date)
        if old is not None:
            new = pd.concat([old[~old['sym'].isin(new['sym'].unique())], new], ignore_index=True)
        store.save_bars(date, new.sort_values(['sym', 'mi']).reset_index(drop=True))
        pr, oldp = profile(tr), store.load_profile(date)
        if oldp is not None:
            pr = pd.concat([oldp[~oldp['sym'].isin(pr['sym'].unique())], pr], ignore_index=True)
        store.save_profile(date, pr)
        store.job_set(jid, state='تم', done=True, trades=int(len(tr)), stocks=int(tr['sym'].nunique()), chain=[], back='bounce')
    except Exception as e:
        store.job_set(jid, state='حصل خطأ', done=True, error=_friendly(e))


def last_us_session():
    """آخر يوم تداول خلص (من غير ما نحسب الأجازات الرسمية)."""
    now = pd.Timestamp.now(tz=TZ)
    d = now.date()
    if now.time() < dt.time(16, 30):
        d -= dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()
