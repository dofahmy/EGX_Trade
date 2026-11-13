# -*- coding: utf-8 -*-
"""شرط مؤشر المدى (ATR STD بتاع تريدنج فيو، الطول ٩):
المؤشر = متوسط (أعلى سعر − أوطى سعر) لآخر ٩ جلسات، والمدى بيحسب إقفال امبارح جواه (زي تريدنج فيو في البورصة المصرية)،
على الأسعار المتعدلة.
الإشارة: المؤشر يعدّي النهارده أعلى قيمة وصلها حوالين قمة السعر (٥ جلسات قبلها و٥ بعدها)،
والقمة = أعلى إقفال في آخر ١٢٠ جلسة، والسعر تحتها بـ٢٠٪ أو أكتر، وطالع ٥٪ أو أكتر من أوطى إقفال في آخر ٢٠ جلسة.
"""
import numpy as np
import pandas as pd
import flowsig
import tvdaily

LEN = 9
LOOK = 120
WIN = 5
BELOW = -20.0
BOUNCE = 5.0
MIN_VAL = 5e5
_CACHE = {}


def signals(store):
    t, _ = flowsig.table(store)
    if t.empty:
        return pd.DataFrame(columns=['sym', 'date'])
    tv = tvdaily.load(store.root)
    key = (str(getattr(store, 'root', '')), len(t), t['date'].max(), len(tv), tv['date'].max() if len(tv) else None)
    if key in _CACHE:
        return _CACHE[key]
    # الأسعار: من تريدنج فيو لو موجودة (الإقفال الرسمي، ومتعدلة زي الشارت)، وإلا من سيجما متعدلة
    t = t[['sym', 'date', 'open', 'hi', 'lo', 'last', 'prev', 'val_m']].copy()
    if len(tv):
        v = tv.rename(columns={'high': 'hi', 'low': 'lo', 'close': 'last'})[['sym', 'date', 'open', 'hi', 'lo', 'last']].copy()
        v = v.sort_values(['sym', 'date'])
        v['prev'] = v.groupby('sym')['last'].shift(1)
        v = v.merge(t[['sym', 'date', 'val_m']], on=['sym', 'date'], how='left')
        t = pd.concat([v, t[~t['sym'].isin(set(v['sym']))]], ignore_index=True)
    out = []
    for sym, x in t.sort_values(['sym', 'date']).groupby('sym'):
        c = x['last'].to_numpy(float)
        pc = x['prev'].to_numpy(float)
        hi = np.fmax(x['hi'].to_numpy(float), pc)
        lo = np.fmin(x['lo'].to_numpy(float), pc)
        b = pd.Series(hi - lo).rolling(LEN, min_periods=LEN).mean().to_numpy()
        val = x['val_m'].ffill().to_numpy(float) * 1e6
        vm = pd.Series(val).rolling(20, min_periods=10).median().to_numpy()
        lo20 = pd.Series(c).rolling(20, min_periods=15).min().to_numpy()
        n = len(c)
        pb = np.full(n, np.nan)
        pk = np.full(n, np.nan)
        for i in range(n):
            s0 = max(0, i - LOOK)
            if i - s0 < 30:
                continue
            j = s0 + int(np.nanargmax(c[s0:i]))
            w = b[max(0, j - WIN):min(i, j + WIN + 1)]
            if np.isfinite(w).any():
                pb[i] = np.nanmax(w)
                pk[i] = c[j]
        for i in range(1, n):
            if not (np.isfinite(pb[i]) and np.isfinite(pb[i - 1]) and np.isfinite(b[i]) and np.isfinite(b[i - 1])):
                continue
            if b[i] >= pb[i] and b[i - 1] < pb[i - 1] and vm[i] >= MIN_VAL:
                below = (c[i] / pk[i] - 1) * 100
                bounce = (c[i] / lo20[i] - 1) * 100
                if below <= BELOW and bounce >= BOUNCE:
                    out.append(dict(sym=sym, date=x['date'].iloc[i], close=c[i], below=below, bounce=bounce))
    r = pd.DataFrame(out, columns=['sym', 'date', 'close', 'below', 'bounce'])
    _CACHE.clear()
    _CACHE[key] = r
    return r
