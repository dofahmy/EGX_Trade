"""
حساب المستويات والإشارات من ملفات "تنفيذات الجلسة".

الفكرة:
- لكل سهم في الجلسة، نبص على صفقات البيع (المشتري هو اللي اتنفذ عليه) عند كل سعر.
- المستوى = سعر اتنفذ عنده بيع في عدد كبير من فترات الخمس دقايق المختلفة (افتراضيًا ٢٠ أو أكتر).
- المستوى "ليه معنى" لو السهم اتداول على عدد أسعار أكبر من حد معين في اليوم (افتراضيًا أكتر من ٦).
- عدد الإشارات في اليوم = عدد الأسهم اللي فيها مستوى من النوع ده.
- القاعدة: لما العدد ينزل تحت الحد (١٠) وبعدين يرجع يبقى الحد أو أكتر، أسهم يوم الرجوع تتشترى مع فتح الجلسة اللي بعده.
"""
import os, json, shutil, subprocess, tempfile, datetime as dt
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

COLS = ['sym', 'name', 'time', 'price', 'vol', 'chg', 'chgp', 'side', 'val', 'cnt', 'seq']
KEEP_MIN_WIN = 10   # بنخزن أي مستوى من ١٠ فترات وطالع، عشان لو غيرتي الإعدادات بعدين


# ---------- قراءة الملف ----------

def extract_xlsx(path: Path, workdir: Path) -> Path:
    """لو الملف مضغوط (rar أو zip) نطلع منه ملف الإكسل."""
    suffix = path.suffix.lower()
    if suffix in ('.xlsx', '.xlsm'):
        return path
    out = workdir / 'ex'
    out.mkdir(parents=True, exist_ok=True)
    if suffix == '.zip':
        shutil.unpack_archive(str(path), str(out))
    else:
        r = subprocess.run(['unar', '-q', '-f', '-o', str(out), str(path)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError('تعذر فك الملف المضغوط: ' + (r.stderr or r.stdout)[:300])
    found = [p for p in out.rglob('*') if p.suffix.lower() in ('.xlsx', '.xlsm')]
    if not found:
        raise RuntimeError('مفيش ملف إكسل جوه الملف المضغوط')
    return max(found, key=lambda p: p.stat().st_size)


def _to_time(v):
    if isinstance(v, dt.time):
        return v
    if isinstance(v, dt.datetime):
        return v.time()
    if isinstance(v, str):
        return dt.datetime.strptime(v.strip(), '%H:%M:%S').time()
    if isinstance(v, (int, float)):           # إكسل ساعات بيخزن الوقت ككسر من اليوم
        secs = int(round(float(v) * 86400))
        return (dt.datetime.min + dt.timedelta(seconds=secs)).time()
    return None


def read_trades(xlsx: Path) -> pd.DataFrame:
    wb = load_workbook(xlsx, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = []
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i == 0 or r is None or r[0] in (None, ''):
            continue
        rows.append(r[:11])
    wb.close()
    df = pd.DataFrame(rows, columns=COLS[:len(rows[0])] if rows else COLS)
    df = df.dropna(subset=['sym', 'price', 'vol'])
    df['sym'] = df['sym'].astype(str).str.strip()
    df['name'] = df['name'].astype(str).str.strip()
    df['time'] = df['time'].map(_to_time)
    df = df.dropna(subset=['time'])
    for c in ('price', 'vol', 'chg', 'val'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['side'] = df['side'].astype(str).str.strip().str.upper()
    df['secs'] = df['time'].map(lambda t: t.hour * 3600 + t.minute * 60 + t.second)

    # ترتيب الصفقات: بالتسلسل لو موجود، ولو مش موجود نتأكد إن الملف مش معكوس
    seq = pd.to_numeric(df.get('seq'), errors='coerce') if 'seq' in df else None
    if seq is not None and seq.notna().mean() > 0.9 and seq.nunique() > 0.9 * len(df):
        df['seq'] = seq
    else:
        df = df.reset_index(drop=True)
        if df['secs'].iloc[0] > df['secs'].iloc[-1]:
            df = df.iloc[::-1].reset_index(drop=True)
        df['seq'] = range(1, len(df) + 1)
    return df.sort_values('seq').reset_index(drop=True)


# ---------- الحساب ----------

def day_stats(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby('sym', sort=False)
    first = g.first()
    st = pd.DataFrame({
        'name': first['name'],
        'n': g.size(),
        'val_m': (g['val'].sum() / 1e6).round(2),
        'prev': (first['price'] - first['chg']).round(4),
        'open': first['price'],
        'lo': g['price'].min(),
        'hi': g['price'].max(),
        'last': g['price'].last(),
        'nprices': g['price'].nunique(),
        'vol': g['vol'].sum(),
    })
    st['chg'] = ((st['last'] / st['prev'] - 1) * 100).round(2)
    return st


def levels(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d['w'] = d['secs'] // 300
    s = d[d['side'] == 'S']
    lv = s.groupby(['sym', 'price']).agg(win=('w', 'nunique'), sell_vol=('vol', 'sum')).reset_index()
    tot = d.groupby('sym')['vol'].sum()
    lv['share'] = (lv['sell_vol'] / lv['sym'].map(tot) * 100).round(2)
    return lv[lv['win'] >= KEEP_MIN_WIN].reset_index(drop=True)


# ---------- التخزين ----------

class Store:
    def __init__(self, root):
        self.root = Path(root)
        (self.root / 'sessions').mkdir(parents=True, exist_ok=True)
        (self.root / 'jobs').mkdir(parents=True, exist_ok=True)

    def sdir(self, date):
        return self.root / 'sessions' / date

    def dates(self):
        return sorted(p.name for p in (self.root / 'sessions').iterdir()
                      if (p / 'stats.csv').exists() and (p / 'levels.csv').exists())

    def load(self, date):
        d = self.sdir(date)
        st = pd.read_csv(d / 'stats.csv', index_col='sym', dtype={'sym': str})
        lv = pd.read_csv(d / 'levels.csv', dtype={'sym': str})
        meta = json.loads((d / 'meta.json').read_text(encoding='utf-8')) if (d / 'meta.json').exists() else {}
        return st, lv, meta

    def delete(self, date):
        shutil.rmtree(self.sdir(date), ignore_errors=True)

    def save(self, date, st, lv, meta):
        d = self.sdir(date)
        d.mkdir(parents=True, exist_ok=True)
        st.to_csv(d / 'stats.csv', index_label='sym')
        lv.to_csv(d / 'levels.csv', index=False)
        (d / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')

    # حالة الرفع
    def job_set(self, jid, **kw):
        p = self.root / 'jobs' / f'{jid}.json'
        cur = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
        cur.update(kw)
        p.write_text(json.dumps(cur, ensure_ascii=False), encoding='utf-8')

    def job_get(self, jid):
        p = self.root / 'jobs' / f'{jid}.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else None


def chain_check(store: Store, date: str, st: pd.DataFrame):
    """نتأكد إن التاريخ صح: إقفال الجلسة اللي قبله لازم يساوي الإقفال السابق في الجلسة دي."""
    ds = [d for d in store.dates() if d != date]
    before = [d for d in ds if d < date]
    after = [d for d in ds if d > date]
    msgs = []
    if before:
        p, _, _ = store.load(before[-1])
        c = p.index.intersection(st.index)
        if len(c):
            m = ((p.loc[c, 'last'] - st.loc[c, 'prev']).abs() <= p.loc[c, 'last'] * 0.006).mean() * 100
            msgs.append(f'تطابق مع جلسة {before[-1]}: {m:.0f}%')
    if after:
        n, _, _ = store.load(after[0])
        c = n.index.intersection(st.index)
        if len(c):
            m = ((st.loc[c, 'last'] - n.loc[c, 'prev']).abs() <= st.loc[c, 'last'] * 0.006).mean() * 100
            msgs.append(f'تطابق مع جلسة {after[0]}: {m:.0f}%')
    return msgs


def process_upload(store: Store, jid: str, path: Path, date: str):
    try:
        store.job_set(jid, state='بفك الملف', date=date)
        with tempfile.TemporaryDirectory() as tmp:
            x = extract_xlsx(path, Path(tmp))
            store.job_set(jid, state='بقرا الصفقات (ممكن ياخد دقيقة أو اتنين)')
            df = read_trades(x)
        store.job_set(jid, state='بحسب المستويات')
        st, lv = day_stats(df), levels(df)
        msgs = chain_check(store, date, st)
        meta = {'date': date, 'trades': int(len(df)), 'stocks': int(len(st)),
                'uploaded_at': dt.datetime.now().isoformat(timespec='seconds'), 'chain': msgs}
        store.save(date, st, lv, meta)
        store.job_set(jid, state='تم', done=True, trades=int(len(df)), stocks=int(len(st)), chain=msgs)
    except Exception as e:
        store.job_set(jid, state='حصل خطأ', done=True, error=str(e)[:500])
    finally:
        try:
            path.unlink()
        except Exception:
            pass


# ---------- الإشارات والقاعدة ----------

def signals_for(st, lv, min_win, min_prices, max_win=None):
    w = lv[lv['win'] >= min_win]
    if max_win:
        w = w[w['win'] <= max_win]
    x = w.merge(st[['nprices']], left_on='sym', right_index=True)
    x = x[x['nprices'] >= min_prices]
    # أقوى مستوى لكل سهم
    x = x.sort_values(['sym', 'win', 'sell_vol'], ascending=[True, False, False]).drop_duplicates('sym')
    x = x.join(st[['name', 'last', 'chg', 'val_m', 'lo', 'hi']], on='sym')
    return x.sort_values('val_m', ascending=False).reset_index(drop=True)


METHODS = {
    'dip': 'الطريقة الأولى: العدد ينزل تحت الحد ويرجع فوقه',
    'double': 'الطريقة التانية: العدد يبقى ضعف اليوم اللي قبله أو أكتر',
}


def analyze(store: Store, min_win=20, min_prices=7, threshold=10, hold=2, mult=2.0, gap=3.0,
            max_win=None, gap_max=None):
    """
    hold = عدد الجلسات اللي السهم بيتشال فيها، والجلسة اللي اتشرى فيها محسوبة.
    hold=2 (الافتراضي): إشارة التلات، الشراء مع فتح الأربعاء، والبيع مع آخر سعر يوم الخميس.

    الطريقة الأولى (dip): العدد ينزل تحت threshold، وأول يوم يرجع فيه threshold أو أكتر = إشارة.
    الطريقة التانية (double): عدد اليوم >= mult × عدد اليوم اللي قبله = إشارة.
    min_win / max_win = عدد مرات المستوى من / لـ (max_win فاضي = من غير حد أعلى).
    gap / gap_max = شرط الفتح: السهم يتشرى بس لو فتح فوق المستوى بنسبة من gap لـ gap_max
    (gap = ٠ وgap_max فاضي = من غير شرط).
    """
    dates = store.dates()
    data = {d: store.load(d) for d in dates}
    days = []
    armed = False
    prev_n = None
    for i, d in enumerate(dates):
        st, lv, meta = data[d]
        sig = signals_for(st, lv, min_win, min_prices, max_win)
        n = len(sig)
        t_dip = armed and n >= threshold
        if n < threshold:
            armed = True
        elif t_dip:
            armed = False
        t_double = prev_n is not None and n > 0 and n >= mult * max(prev_n, 1)
        days.append(dict(date=d, count=n, prev=prev_n, trig={'dip': t_dip, 'double': t_double},
                         armed_after=armed, meta=meta, sig=sig))
        prev_n = n

    def build(i, day):
        buy_i, sell_i = i + 1, i + hold
        res = dict(signal_date=day['date'],
                   buy_date=dates[buy_i] if buy_i < len(dates) else None,
                   sell_date=dates[sell_i] if sell_i < len(dates) else None,
                   rows=[], avg=None, market=None)
        rows = []
        for r in day['sig'].itertuples():
            row = dict(sym=r.sym, name=r.name, level=r.price, win=int(r.win), share=r.share,
                       close=r.last, val_m=r.val_m, entry=None, exit=None, ret=None,
                       min_open=round(r.price * (1 + gap / 100), 4), gap=None, ok=None)
            if res['buy_date'] and r.sym in data[res['buy_date']][0].index:
                row['entry'] = float(data[res['buy_date']][0].loc[r.sym, 'open'])
                row['gap'] = round((row['entry'] / r.price - 1) * 100, 2)
                row['ok'] = row['gap'] >= gap and (gap_max is None or row['gap'] <= gap_max)
            if res['buy_date'] and not res['sell_date'] and row['entry'] and r.sym in data[dates[-1]][0].index:
                # صفقة لسه مفتوحة: نقيّمها على آخر سعر في آخر جلسة مرفوعة
                row['mark'] = float(data[dates[-1]][0].loc[r.sym, 'last'])
                row['mret'] = round((row['mark'] / row['entry'] - 1) * 100, 2)
            if res['sell_date'] and row['entry'] and r.sym in data[res['sell_date']][0].index:
                row['exit'] = float(data[res['sell_date']][0].loc[r.sym, 'last'])
                row['ret'] = round((row['exit'] / row['entry'] - 1) * 100, 2)
            rows.append(row)
        res['rows'] = rows
        rets = [r['ret'] for r in rows if r['ret'] is not None]
        if rets:
            res['avg'] = round(sum(rets) / len(rets), 2)
            res['wins'] = sum(1 for x in rets if x > 0)
            res['n'] = len(rets)
            b = data[res['buy_date']][0]
            s = data[res['sell_date']][0]
            c = b.index[b['n'] >= 60].intersection(s.index)
            res['market'] = round(float(((s.loc[c, 'last'] / b.loc[c, 'open'] - 1) * 100).median()), 2)
            okr = [r['ret'] for r in rows if r['ret'] is not None and r['ok']]
            res['ok_n'] = len(okr)
            res['ok_avg'] = round(sum(okr) / len(okr), 2) if okr else None
            res['ok_wins'] = sum(1 for x in okr if x > 0)
        mr = [r for r in rows if r.get('mret') is not None]
        if mr:
            res['mark_date'] = dates[-1]
            res['open_n'] = len(mr)
            res['open_avg'] = round(sum(r['mret'] for r in mr) / len(mr), 2)
            res['open_wins'] = sum(1 for r in mr if r['mret'] > 0)
            okm = [r['mret'] for r in mr if r['ok']]
            res['open_ok_n'] = len(okm)
            res['open_ok_avg'] = round(sum(okm) / len(okm), 2) if okm else None
            res['open_ok_wins'] = sum(1 for x in okm if x > 0)
            b = data[res['buy_date']][0]
            s = data[dates[-1]][0]
            c = b.index[b['n'] >= 60].intersection(s.index)
            res['open_market'] = round(float(((s.loc[c, 'last'] / b.loc[c, 'open'] - 1) * 100).median()), 2)
        return res

    trades = {m: [build(i, day) for i, day in enumerate(days) if day['trig'][m]] for m in METHODS}

    state = {}
    if days:
        last = days[-1]
        state['dip'] = 'buy' if last['trig']['dip'] else ('armed' if last['armed_after'] else 'idle')
        state['double'] = 'buy' if last['trig']['double'] else 'idle'
    return days, trades, state
