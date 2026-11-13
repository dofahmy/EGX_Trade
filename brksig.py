# -*- coding: utf-8 -*-
"""كسر بعد تجميع طويل:
السهم كان في مدى ضيق: أعلى إقفال في آخر ١٢٠ جلسة مش أكتر من أوطى إقفال بـ٤٠٪،
والنهارده طلع ٨٪ أو أكتر، بقيمة تداول ٥ أضعاف المعتاد (وسيط آخر ٦٠ جلسة) أو أكتر،
وقفل فوق أعلى إقفال في المدى. الأسعار من تريدنج فيو لو موجودة، وإلا من سيجما متعدلة.
"ثابت" = بعد ٥ جلسات السعر لسه فوق مستوى الكسر.
"""
import numpy as np
import pandas as pd
import flowsig
import tvdaily

LOOK = 120
RNG = 40.0
CHG = 8.0
VX = 5.0
MIN_VAL = 5e5
HOLD = 5
_CACHE = {}


def signals(store):
    t, _ = flowsig.table(store)
    if t.empty:
        return pd.DataFrame(columns=['sym', 'date', 'held'])
    tv = tvdaily.load(store.root)
    key = (str(getattr(store, 'root', '')), len(t), t['date'].max(), len(tv), tv['date'].max() if len(tv) else None)
    if key in _CACHE:
        return _CACHE[key]
    t = t[['sym', 'date', 'last', 'val_m']].copy()
    if len(tv):
        v = tv.rename(columns={'close': 'last'})[['sym', 'date', 'last']].copy()
        v = v.merge(t[['sym', 'date', 'val_m']], on=['sym', 'date'], how='left')
        t = pd.concat([v, t[~t['sym'].isin(set(v['sym']))]], ignore_index=True)
    out = []
    for sym, x in t.sort_values(['sym', 'date']).groupby('sym'):
        c = x['last'].to_numpy(float)
        val = x['val_m'].to_numpy(float) * 1e6
        vs = pd.Series(val)
        med60 = vs.shift(1).rolling(60, min_periods=30).median().to_numpy()
        med20 = vs.rolling(20, min_periods=10).median().to_numpy()
        cs = pd.Series(c).shift(1)
        hi = cs.rolling(LOOK, min_periods=LOOK).max().to_numpy()
        lo = cs.rolling(LOOK, min_periods=LOOK).min().to_numpy()
        dates = x['date'].to_numpy()
        n = len(c)
        for i in range(1, n):
            if not (np.isfinite(hi[i]) and lo[i] > 0 and c[i - 1] > 0 and med60[i] > 0):
                continue
            chg = (c[i] / c[i - 1] - 1) * 100
            rng = (hi[i] / lo[i] - 1) * 100
            vx = val[i] / med60[i] if np.isfinite(val[i]) else np.nan
            if chg >= CHG and vx >= VX and rng <= RNG and c[i] > hi[i] and med20[i] >= MIN_VAL:
                held = None
                if i + HOLD < n:
                    held = bool(c[i + HOLD] > hi[i])
                out.append(dict(sym=sym, date=dates[i], chg=chg, vx=vx, rng=rng, level=hi[i], held=held))
    r = pd.DataFrame(out, columns=['sym', 'date', 'chg', 'vx', 'rng', 'level', 'held'])
    _CACHE.clear()
    _CACHE[key] = r
    return r
