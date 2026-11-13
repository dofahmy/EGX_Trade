# -*- coding: utf-8 -*-
"""الكبار بيبيعوا: أسبوع تداول السهم فيه ٣ أضعاف معتاده أو أكتر (بعد ما نشيل حركة السوق)،
وصافي الصفقات الكبيرة بيع ٥٪ أو أكتر من تداول الأسبوع.

اتختبرت على سبتمبر ٢٠٢٥ لنوفمبر ٢٠٢٦: السهم اتأخر عن السوق الأسبوع اللي بعده في ٥٧٪ من المرات،
وبعد ٤ أسابيع كان أقل من السوق بحوالي ٢٫٤٪ (وسيط)، والنتيجة متكررة في النصين.
حد الصفقة الكبيرة متحرك: وسيط أعلى ١٠٪ من قيم صفقات السهم في آخر ٦٠ جلسة قبل اليوم.
"""
import numpy as np
import pandas as pd
import engine

MULT = 3.0
SELL = -5.0
MIN_VAL = 1e6
_CACHE = {}


def daily(store):
    dates = store.dates()
    try:
        stamp = tuple((d, (store.sdir(d) / 'stats.csv').stat().st_mtime) for d in dates)
    except Exception:
        stamp = None
    key = str(getattr(store, 'root', id(store)))
    if stamp is not None and key in _CACHE and _CACHE[key][0] == stamp:
        return _CACHE[key][1]
    sd = engine.seed2().set_index(['date', 'sym'])
    parts = []
    for d in dates:
        try:
            st = pd.read_csv(store.sdir(d) / 'stats.csv', dtype={'sym': str})
        except Exception:
            continue
        x = pd.DataFrame({'date': d, 'sym': st['sym'], 'name': st.get('name', ''), 'c': st['last'], 'val': st['val_m'] * 1e6})
        for c in ('bigb2', 'bigs2'):
            x[c] = st[c] if c in st else np.nan
        if x['bigb2'].isna().all() and len(sd):
            idx = pd.MultiIndex.from_frame(x[['date', 'sym']])
            for c in ('bigb2', 'bigs2'):
                x[c] = sd[c].reindex(idx).to_numpy()
        parts.append(x)
    t = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if len(t):
        import flowsig
        ac = flowsig.adj_close(store)
        if len(ac):
            v = ac.reindex(pd.MultiIndex.from_frame(t[['date', 'sym']])).to_numpy()
            t['c'] = np.where(np.isnan(v), t['c'], v)
    if stamp is not None:
        _CACHE[key] = (stamp, t)
    return t


def weeks(store):
    t = daily(store)
    if t.empty:
        return pd.DataFrame(), {}
    t = t.copy()
    t['wk'] = pd.to_datetime(t['date']).dt.to_period('W-THU')
    t['bn'] = t['bigb2'] - t['bigs2']
    w = t.groupby(['sym', 'wk']).agg(val=('val', 'sum'), bn=('bn', 'sum'), bok=('bigb2', lambda v: v.notna().all()),
                                     c=('c', 'last'), days=('date', 'nunique'), name=('name', 'last')).reset_index()
    w = w[(w['days'] >= 2) & (w['val'] > 0)].sort_values(['sym', 'wk'])
    w['vday'] = w['val'] / w['days']
    m = w.groupby('wk')['val'].sum() / w.groupby('wk')['days'].max()
    mm = m / m.shift(1).rolling(12, min_periods=4).median()
    w['base'] = w.groupby('sym')['vday'].transform(lambda v: v.shift(1).rolling(12, min_periods=4).median())
    w['mult'] = w['vday'] / w['base'] / w['wk'].map(mm)
    w['bnr'] = np.where(w['bok'], w['bn'] / w['val'] * 100, np.nan)
    g = w.groupby('sym')
    w['ret'] = g['c'].pct_change() * 100
    w['n1'] = g['ret'].shift(-1)
    w['n4'] = (g['c'].shift(-4) / w['c'] - 1) * 100
    liq = w['base'] >= MIN_VAL
    for c in ('n1', 'n4'):
        w['x' + c] = w[c] - w[liq].groupby('wk')[c].transform('median').reindex(w.index)
    w['sig'] = liq & (w['mult'] >= MULT) & (w['bnr'] <= SELL)
    w = w.replace([np.inf, -np.inf], np.nan)
    s = w[w['sig']]
    def st(col):
        z = s[col].dropna()
        return dict(n=int(len(z)), under=round(float((z < 0).mean() * 100)) if len(z) else None,
                    med=round(float(z.median()), 2) if len(z) else None)
    days_in = t.groupby(t['wk'])['date'].nunique()
    return w, dict(w1=st('xn1'), w4=st('xn4'), days=days_in.to_dict())


def page(store, n=8):
    w, summ = weeks(store)
    if w.empty:
        return [], summ
    out = []
    allw = sorted(w['wk'].unique(), reverse=True)
    f = lambda v, k=1: None if pd.isna(v) else round(float(v), k)
    for k in allw[:n] if n else allw:
        z = w[(w['wk'] == k) & w['sig']].sort_values('mult', ascending=False)
        rows = [dict(sym=r.sym, name=str(r.name) if isinstance(r.name, str) else '', mult=f(r.mult), bnr=f(r.bnr),
                     ret=f(r.ret), val=f(r.val / 1e6, 0), x1=f(r.xn1), x4=f(r.xn4)) for r in z.itertuples()]
        nd = int(summ['days'].get(k, 0))
        out.append(dict(start=str(k.start_time.date()), end=str(k.end_time.date()), days=nd, rows=rows))
    return out, summ
