# -*- coding: utf-8 -*-
"""تريدة ٥ أيام: دايفرجنس إيجابي على CCI ٤٠ (على (فتح + أعلى + أوطى + إقفال) ÷ ٤، والأصفر متوسط بسيط ٢٨).
  - الأزرق عمل قاع تحت −١٥٠، وبعده بـ٥ جلسات أو أكتر (في خلال ٤٥ جلسة) السعر عمل قاع أوطى، والأزرق فيه أعلى بـ٣٠ نقطة+.
  - يوم الإشارة: أول يوم في خلال ١٥ جلسة من قاع السعر الأزرق فيه فوق الأصفر وفوق −١٠٠ وطالع، والأصفر تحت −٨٠، والشمعة خضرا.
  - "قوية": السوق كله نازل ١٥٪+ من أعلى نقطة في سنة، والأصفر تحت −١٢٠.
النتيجة: الإقفال بعد ٥ جلسات، وأعلى سعر في الـ٥ جلسات.
"""
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view as swv

import tvdaily

L, M = 40, 28
_CACHE = {}


def cci(o, h, l, c):
    src = (o + h + l + c) / 4
    k = np.full(len(src), np.nan)
    if len(src) >= L:
        w = swv(src, L)
        mu = w.mean(1)
        mad = np.abs(w - mu[:, None]).mean(1)
        with np.errstate(all='ignore'):
            k[L - 1:] = (src[L - 1:] - mu) / (0.015 * mad)
    k[~np.isfinite(k)] = np.nan
    return k, pd.Series(k).rolling(M).mean().to_numpy()


def _one(dates, o, h, l, c, val, mdd):
    k, m = cci(o, h, l, c)
    n = len(c)
    vm = pd.Series(val).rolling(20, min_periods=10).median().to_numpy()
    out, last = [], -99
    for t2 in range(50, n - 1):
        if c[t2] != np.min(c[max(0, t2 - 5):t2 + 6]) or not np.isfinite(k[t2]):
            continue
        lo, hi = max(0, t2 - 45), t2 - 5
        if hi <= lo or not np.isfinite(k[lo:hi]).any():
            continue
        t1 = lo + int(np.nanargmin(k[lo:hi]))
        if not (k[t1] <= -150 and c[t2] < c[t1] and k[t2] > k[t1] + 30):
            continue
        for i in range(t2 + 1, min(n, t2 + 16)):
            if np.isfinite(m[i]) and k[i] > m[i] and k[i] > -100 and k[i] > k[i - 1] and c[i] > o[i] and m[i] <= -80:
                if i - last < 15:
                    break
                last = i
                f5, h5 = c[i + 1:i + 6], h[i + 1:i + 6]
                md = float(mdd[i]) if np.isfinite(mdd[i]) else None
                out.append(dict(d=dates[i], c=round(float(c[i]), 3), ki=round(float(k[i]), 1), ym=round(float(m[i]), 1),
                                d1=dates[t1], k1=round(float(k[t1])), c1=round(float(c[t1]), 3),
                                d2=dates[t2], k2=round(float(k[t2])), c2=round(float(c[t2]), 3),
                                md=None if md is None else round(md, 1), n=int(len(f5)),
                                r5=round(float((f5[-1] / c[i] - 1) * 100), 1) if len(f5) else None,
                                h5=round(float((np.max(h5) / c[i] - 1) * 100), 1) if len(h5) else None,
                                hp=round(float(np.max(h5)), 3) if len(h5) else None,
                                liq=bool(vm[i] >= 5e5) if np.isfinite(vm[i]) else False,
                                st=bool(md is not None and md <= -15 and m[i] <= -120)))
                break
    return out


def signals(store):
    root = store.root
    d = tvdaily.load(root)
    if d is None or not len(d):
        return []
    key = (str(root), len(d), str(d['date'].max()))
    if key in _CACHE:
        return _CACHE[key]
    d = d.sort_values(['sym', 'date']).reset_index(drop=True)
    d['date'] = d['date'].astype(str).str[:10]
    ret = d.groupby('sym')['close'].pct_change().clip(-0.3, 0.3)
    mk = ret.groupby(d['date']).mean().fillna(0).sort_index()
    idx = (1 + mk).cumprod()
    mdd = (idx / idx.rolling(250, min_periods=50).max() - 1) * 100
    out = []
    for sym, z in d.groupby('sym'):
        if len(z) < 200:
            continue
        c = z['close'].astype(float).to_numpy()
        dates = z['date'].to_numpy()
        for r in _one(dates, z['open'].astype(float).to_numpy(), z['high'].astype(float).to_numpy(),
                      z['low'].astype(float).to_numpy(), c, c * z['volume'].astype(float).to_numpy(), mdd.reindex(dates).to_numpy()):
            r['s'] = sym
            out.append(r)
    out.sort(key=lambda r: r['d'], reverse=True)
    _CACHE.clear()
    _CACHE[key] = out
    return out
