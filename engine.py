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


def bars(df: pd.DataFrame) -> pd.DataFrame:
    """شموع الدقيقة لكل سهم، عشان نعرف ترتيب الحركة جوه اليوم (اتنفذ الأول ولا وصل الهدف الأول)."""
    d = df[['sym', 'secs', 'price', 'seq']].sort_values('seq')
    d = d.assign(mi=d['secs'] // 60)
    g = d.groupby(['sym', 'mi'], sort=False)['price']
    b = pd.DataFrame({'o': g.first(), 'h': g.max(), 'l': g.min(), 'c': g.last()}).reset_index()
    return b.sort_values(['sym', 'mi']).reset_index(drop=True)


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

    def save_bars(self, date, b):
        b.to_csv(self.sdir(date) / 'bars.csv.gz', index=False, compression='gzip')

    def load_bars(self, date):
        p = self.sdir(date) / 'bars.csv.gz'
        return pd.read_csv(p, dtype={'sym': str}) if p.exists() else None

    def delete(self, date):
        shutil.rmtree(self.sdir(date), ignore_errors=True)

    def save(self, date, st, lv, meta, bars=None):
        d = self.sdir(date)
        d.mkdir(parents=True, exist_ok=True)
        if bars is not None:
            bars.to_csv(d / 'bars.csv.gz', index=False, compression='gzip')
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
        store.save(date, st, lv, meta, bars(df))
        store.job_set(jid, state='تم', done=True, trades=int(len(df)), stocks=int(len(st)), chain=msgs)
    except Exception as e:
        store.job_set(jid, state='حصل خطأ', done=True, error=str(e)[:500])
    finally:
        try:
            path.unlink()
        except Exception:
            pass


# ---------- الإشارات والقاعدة ----------

def signals_for(st, lv, min_win, min_prices, max_win=None, min_share=0, max_share=None):
    w = lv[lv['win'] >= min_win]
    if max_win:
        w = w[w['win'] <= max_win]
    if min_share:
        w = w[w['share'] >= min_share]
    if max_share is not None:
        w = w[w['share'] < max_share]
    x = w.merge(st[['nprices']], left_on='sym', right_index=True).reset_index(drop=True)
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
            max_win=None, gap_max=None, day_win=20, min_share=10.0, warn_share=2.0, **_):
    """
    hold = عدد الجلسات اللي السهم بيتشال فيها، والجلسة اللي اتشرى فيها محسوبة.
    hold=2 (الافتراضي): إشارة التلات، الشراء مع فتح الأربعاء، والبيع مع آخر سعر يوم الخميس.

    الطريقة الأولى (dip): العدد ينزل تحت threshold، وأول يوم يرجع فيه threshold أو أكتر = إشارة.
    الطريقة التانية (double): عدد اليوم >= mult × عدد اليوم اللي قبله = إشارة.
    day_win = عدد مرات المستوى اللي بيتحسب بيه عدد إشارات اليوم (اللي بيحدد اليوم إشارة ولا لأ).
    min_win / max_win = مدى عدد مرات المستوى لاختيار الأسهم اللي هتتشترى من يوم الإشارة
    (max_win فاضي = من غير حد أعلى).
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
        allsig = signals_for(st, lv, day_win, min_prices)            # كل إشارات اليوم
        n = len(allsig)
        # كل سهم من إشارات اليوم ليه حالة واحدة بس، محسوبة على نفس المستوى (أقوى مستوى في السهم):
        # ١. تحذير: الكمية اللي اتلمت عند المستوى أقل من warn_share
        # ٢. قايمة الشراء: مش تحذير، وعدد المرات في المدى، والنسبة min_share أو أكتر
        # ٣. برا الشروط: الباقي
        is_warn = allsig['share'] < warn_share
        in_rng = allsig['win'] >= min_win
        if max_win:
            in_rng &= allsig['win'] <= max_win
        is_buy = ~is_warn & in_rng & (allsig['share'] >= min_share)
        allsig = allsig.assign(status=['warn' if w else 'buy' if b else 'out' for w, b in zip(is_warn, is_buy)])
        sig = allsig[is_buy.values].reset_index(drop=True)      # الأسهم اللي هتتشترى
        warn = allsig[is_warn.values].reset_index(drop=True)    # التحذيرات
        t_dip = armed and n >= threshold
        if n < threshold:
            armed = True
        elif t_dip:
            armed = False
        t_double = prev_n is not None and n > 0 and n >= mult * max(prev_n, 1)
        days.append(dict(date=d, count=n, prev=prev_n, trig={'dip': t_dip, 'double': t_double},
                         armed_after=armed, meta=meta, sig=sig, warn=warn, allsig=allsig))
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

    # نتايج قايمة التحذير: حصل إيه في الجلسة اللي بعدها
    warns = []
    for i, day in enumerate(days):
        nxt = data[dates[i + 1]][0] if i + 1 < len(dates) else None
        st0 = data[day['date']][0]
        mk = None
        if nxt is not None:
            c = nxt.index[nxt['n'] >= 60].intersection(st0.index)
            mk = round(float(((nxt.loc[c, 'last'] / st0.loc[c, 'last'] - 1) * 100).median()), 2)
        rows = []
        for r in day['warn'].itertuples():
            row = dict(sym=r.sym, name=r.name, level=r.price, win=int(r.win), share=r.share, close=r.last,
                       val_m=r.val_m, nlo=None, nclose=None, ret=None, broke=None)
            if nxt is not None and r.sym in nxt.index:
                row['nlo'] = float(nxt.loc[r.sym, 'lo'])
                row['nclose'] = float(nxt.loc[r.sym, 'last'])
                row['ret'] = round((row['nclose'] / r.last - 1) * 100, 2)
                row['broke'] = row['nlo'] < r.price * 0.995
            rows.append(row)
        done = [x for x in rows if x['ret'] is not None]
        warns.append(dict(date=day['date'], next=dates[i + 1] if i + 1 < len(dates) else None, rows=rows,
                          market=mk, n=len(done),
                          avg=round(sum(x['ret'] for x in done) / len(done), 2) if done else None,
                          broke=sum(1 for x in done if x['broke']),
                          down=sum(1 for x in done if mk is not None and x['ret'] < mk)))

    state = {}
    if days:
        last = days[-1]
        state['dip'] = 'buy' if last['trig']['dip'] else ('armed' if last['armed_after'] else 'idle')
        state['double'] = 'buy' if last['trig']['double'] else 'idle'
    return days, trades, state, warns


# ---------- التريدة: شراء وبيع في نفس اليوم ----------

def _sim(entry_limit, tp, b, row):
    """يمشي على شموع الدقيقة. entry_limit=None يعني شراء مع الفتح.
    بيرجع (اتنفذ؟، سعر الشراء، سعر الخروج، سبب الخروج، دقيق؟)."""
    if b is not None and len(b):
        o, h, l, c = b['o'].values, b['h'].values, b['l'].values, b['c'].values
        start = None
        if entry_limit is None:
            start, buy = 0, float(o[0])
        else:
            for k in range(len(b)):
                if l[k] <= entry_limit:
                    start, buy = k, float(min(o[k], entry_limit))
                    break
        if start is None:
            return False, None, None, None, True
        tgt = buy * (1 + tp / 100)
        # في شمعة الشراء نفسها مانعرفش الترتيب، فنعدّ الهدف بس لو الإقفال فوقه، إلا لو الشراء مع الفتح
        if (entry_limit is None and h[start] >= tgt) or c[start] >= tgt:
            return True, buy, round(tgt, 4), 'tp', True
        for k in range(start + 1, len(b)):
            if h[k] >= tgt:
                return True, buy, round(tgt, 4), 'tp', True
        return True, buy, float(c[-1]), 'close', True
    # من غير شموع: من أسعار اليوم بس، فترتيب الحركة مش معروف
    op, lo, hi, last = float(row['open']), float(row['lo']), float(row['hi']), float(row['last'])
    if entry_limit is None:
        buy = op
    elif lo <= entry_limit:
        buy = min(op, entry_limit)
    else:
        return False, None, None, None, False
    tgt = buy * (1 + tp / 100)
    if hi >= tgt:
        return True, buy, round(tgt, 4), 'tp', False
    return True, buy, last, 'close', False


def same_day_trades(store: Store, day_win=20, min_prices=7, tp=3.0, picks=2, exclude=None, **_):
    """لكل جلسة: أكبر سهمين (أو العدد المحدد) في النسبة اللي اتلمت عند المستوى، وإقفالهم موجب.
    الجلسة اللي بعدها: شراء مع الفتح، والبيع مع الهدف أو الإقفال، ومعاه نتيجة الفتح للإقفال."""
    dates = store.dates()
    skip = set((exclude or '').split())
    out = []
    for i, date in enumerate(dates):
        st, lv, _ = store.load(date)
        sig = signals_for(st, lv, day_win, min_prices).sort_values('share', ascending=False)
        sig = sig[(sig['chg'] > 0) & ~sig['sym'].isin(skip)].head(max(1, int(picks)))
        nd = dates[i + 1] if i + 1 < len(dates) else None
        s2 = store.load(nd)[0] if nd else None
        items = []
        for r in sig.itertuples():
            k = dict(sym=r.sym, name=r.name, share=float(r.share), chg=float(r.chg), last=float(r.last))
            n = None
            if nd:
                if r.sym in s2.index:
                    r2 = s2.loc[r.sym]
                    _, buy, ex, why, _ = _sim(None, tp, None, r2)
                    last = float(r2['last'])
                    n = dict(date=nd, buy=buy, target=round(buy * (1 + tp / 100), 4), exit=ex, why=why,
                             res=round((ex / buy - 1) * 100, 2), last=last, oc=round((last / buy - 1) * 100, 2))
                else:
                    n = dict(date=nd, missing=True)
            items.append(dict(pick=k, next=n))
        out.append(dict(date=date, next_date=nd, stocks=items))
    return out


# ---------- قاعدة الارتداد ----------

def late_ret(bars, minutes=30):
    """صعود كل سهم في آخر نص ساعة من الجلسة: من أول سعر في الفترة دي لآخر سعر."""
    if bars is None or not len(bars):
        return None
    late = bars[bars['mi'] >= bars['mi'].max() - minutes + 1].sort_values(['sym', 'mi'])
    g = late.groupby('sym')
    return ((g['c'].last() / g['o'].first() - 1) * 100).round(2)


def _picks(g, col, top=5, keep=3, common=()):
    """أول top سهم بالترتيب، من غيرهم المشترك مع القايمة التانية، وبعدين أقل نزول، وناخد keep."""
    first = g.sort_values(col, ascending=False).head(top)
    return first, first[~first.index.isin(common)].sort_values('chg', ascending=False).head(keep)


def bounce_days(store: Store, b_drop=2.5, b_range=7.0, b_val=0.5, exclude=None, b_maxdrop=10.0, **_):
    """الأسهم اللي نزلت يومين ورا بعض، وتاني يوم نزلت b_drop٪ أو أكتر، والفرق بين أعلى وأقل سعر b_range٪ أو أكتر.
    وبنقارن أداءها الجلسة اللي بعدها بالسوق."""
    dates = store.dates()
    stats = {d: store.load(d)[0] for d in dates}
    out = []
    for i, date in enumerate(dates):
        if i == 0:
            continue
        st, prev = stats[date], stats[dates[i - 1]]
        skip = set((exclude or '').split())
        st = st[(st['val_m'] >= b_val) & ~st['name'].astype(str).str.contains('وثائق') & ~st.index.isin(skip)]
        rng = (st['hi'] / st['lo'] - 1) * 100
        c1 = prev['chg'].reindex(st.index)
        m = (c1 < 0) & (st['chg'] <= -b_drop) & (rng >= b_range)
        if b_maxdrop:
            m &= st['chg'] >= -b_maxdrop   # نزول أكبر من كده غالبًا خبر، فمش بندخل فيه
        g = st[m].assign(chg1=c1[m], rng=rng[m].round(2))
        lr = late_ret(store.load_bars(date))
        g = g.assign(late=lr.reindex(g.index) if lr is not None else float('nan'))
        nd = dates[i + 1] if i + 1 < len(dates) else None
        row = dict(date=date, next_date=nd, n_market=len(st), rows=[])
        if nd:
            nx = stats[nd]
            mk = nx['chg'].reindex(st.index).dropna()
            top = mk.quantile(0.9) if len(mk) else None
            row.update(mk_med=round(float(mk.median()), 2), mk_up=round(float((mk > 0).mean() * 100), 0), top=round(float(top), 2))
        for sym, r in g.sort_values('chg').iterrows():
            it = dict(sym=sym, name=r['name'], chg1=float(r['chg1']), chg=float(r['chg']), rng=float(r['rng']),
                      val_m=float(r['val_m']), last=float(r['last']),
                      late=None if pd.isna(r['late']) else float(r['late']))
            if nd and sym in stats[nd].index:
                n2 = stats[nd].loc[sym]
                it.update(next=float(n2['chg']), n_open=float(n2['open']), n_last=float(n2['last']),
                          oc=round((float(n2['last']) / float(n2['open']) - 1) * 100, 2))
            row['rows'].append(it)
        # الاختيار: أول ٥ في كل ترتيب، من غير المشترك، وأقل نزول، وناخد ٣ من كل قايمة
        # آخر نص ساعة محتاج حركة اليوم لكل أسهم القاعدة. الناقص منها بيتجاب بزرار في الأمريكي
        missing = [x for x in g.index if pd.isna(g.loc[x, 'late'])]
        row['missing_bars'] = missing
        has_late = bool(len(g)) and not missing
        t1 = g.sort_values('late', ascending=False).head(5) if has_late else g.iloc[0:0]
        t2 = g.sort_values('rng', ascending=False).head(5)
        # لو القاعدة طلّعت ٥ أسهم أو أقل، القايمتين هما نفس الأسهم، فمفيش معنى نشيل المشترك
        common = set(t1.index) & set(t2.index) if len(g) > 5 else set()
        by = {x['sym']: x for x in row['rows']}
        row['lists'] = []
        for key, title, t in (('late', 'أكبر صعود في آخر نص ساعة', t1), ('rng', 'أكبر فرق بين أعلى وأقل سعر', t2)):
            pk = t[~t.index.isin(common)].sort_values('chg', ascending=False).head(3)
            items = [by[x] for x in pk.index]
            r_ = [x['next'] for x in items if 'next' in x]
            row['lists'].append(dict(key=key, title=title, top5=list(t.index), items=items,
                                     avail=(key != 'late' or bool(has_late)),
                                     avg=round(sum(r_) / len(r_), 2) if r_ else None,
                                     oc=round(sum(x['oc'] for x in items if 'oc' in x) / len(r_), 2) if r_ else None))
        res = [x['next'] for x in row['rows'] if 'next' in x]
        if nd and res:
            ser = pd.Series(res)
            row.update(g_med=round(float(ser.median()), 2), g_mean=round(float(ser.mean()), 2), g_avg=round(float(ser.mean()), 2),
                       g_up=round(float((ser > 0).mean() * 100), 0), g_top=int((ser >= row['top']).sum()),
                       g_10=int((ser >= 10).sum()), hit=bool(ser.median() > row['mk_med']))
        out.append(row)
    return out
