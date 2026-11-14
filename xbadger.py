# -*- coding: utf-8 -*-
"""أسهم-أكس-باجر: قاع مزدوج على مؤشر CCI (طول ٢٠ على (أعلى + أوطى + إقفال) ÷ ٣، والأصفر = متوسطه البسيط ١٤).
  ١. العد بيبدأ من جديد كل ما الأزرق يوصل +١٠٠ (تشبع شرائي).
  ٢. القاع الأول: الأزرق تحت −١٠٠.
  ٣. القاع التاني بعده بـ٤ لـ٣٥ جلسة والأزرق تحت −٥٠، وبين القاعين الأزرق ارتد (٤٠ نقطة أو أكتر) بس ماوصلش +١٠٠،
     وماعملش قاع أوطى من الأول.
  ٤. السعر عند القاع التاني مش أوطى من الأول بأكتر من ١٥٪ (ولا أعلى بأكتر من ٢٥٪).
  ٥. الإشارة: أول يوم (في خلال ١٢ جلسة من القاع التاني) الأزرق فيه فوق الأصفر وفوق −١٠٠ وطالع.
  ٦. إشارة واحدة بس لحد ما الأزرق يوصل +١٠٠ تاني، إلا لو عمل قاع أوطى من قاع الإشارة (الإشارة فشلت، نعد من الأول).
السهم بيتلوّن لو عمل طلعة بكمية كبيرة مع الإشارة: يوم من يوم الإشارة لحد ١٠ جلسات بعدها طلع فيه ٨٪ أو أكتر
بقيمة تداول ٥ أضعاف المعتاد (وسيط آخر ٦٠ جلسة) أو أكتر.
الأسعار من سيجما متعدلة، والمدى بيحسب إقفال امبارح جواه زي تريدنج فيو.
"""
import numpy as np
import pandas as pd
import flowsig

OS, OB = -100.0, 100.0
W = 2
NEAR = 12
GAPMIN, GAPMAX = 4, 35
BOUNCE = 40.0
PLO, PHI = 0.85, 1.25
T2MAX = -50.0
WAIT = 10
CHG = 8.0
VX = 5.0
MIN_VAL = 5e5
_CACHE = {}
COLS = ['sym', 'date', 'close', 't1', 't2', 'big', 'bigchg', 'bigvx', 'mx', 'mxn', 'days', 'now']


def _mad(v):
    return np.mean(np.abs(v - v.mean()))


def cci(hi, lo, cc):
    """CCI ٢٠ على (أعلى + أوطى + إقفال) ÷ ٣ (المدى بيحسب إقفال امبارح جواه زي تريدنج فيو)، والأصفر متوسطه ١٤."""
    pc = np.r_[np.nan, cc[:-1]]
    h = np.fmax(hi, pc)
    l = np.fmin(lo, pc)
    src = pd.Series((h + l + cc) / 3)
    dev = src.rolling(20).apply(_mad, raw=True).replace(0, np.nan)
    c = ((src - src.rolling(20).mean()) / (0.015 * dev)).to_numpy()
    m = pd.Series(c).rolling(14).mean().to_numpy()
    return c, m, h, l


def wsig(c, m, cc):
    """إشارات القاع المزدوج: [(i يوم الإشارة، k1 القاع الأول، k2 القاع التاني)]."""
    n = len(cc)
    loc = [k for k in range(W, n - 1) if np.isfinite(c[k]) and c[k] <= T2MAX and c[k] == np.nanmin(c[k - W:k + W + 1])]
    cand = {}
    for k2 in loc:
        best = None
        for k1 in [k for k in loc if k2 - GAPMAX <= k <= k2 - GAPMIN and c[k] <= OS]:
            mid = c[k1 + 1:k2]
            bmax = np.nanmax(mid)
            pr = cc[k2] / cc[k1]
            if bmax - c[k1] >= BOUNCE and bmax - c[k2] >= BOUNCE / 2 and bmax < OB and PLO <= pr <= PHI and np.nanmin(mid) >= c[k1]:
                best = k1
        if best is None:
            continue
        for i in range(k2 + 1, min(n, k2 + 1 + NEAR)):
            if np.isfinite(m[i]) and c[i] > m[i] and c[i] > OS and c[i] > c[i - 1]:
                cand.setdefault(i, (best, k2))
                break
    out = []
    armed, floor = True, None
    for i in range(n):
        if np.isfinite(c[i]) and c[i] >= OB:
            armed = True
        if not armed and floor is not None and np.isfinite(c[i]) and c[i] < floor:
            armed = True
        if i in cand and armed:
            k1, k2 = cand[i]
            armed, floor = False, min(c[k1], c[k2])
            out.append((i, k1, k2))
    return out


def _one(dates, hi, lo, cc, val):
    n = len(cc)
    c, m, _, _ = cci(hi, lo, cc)
    pc = np.r_[np.nan, cc[:-1]]
    vs = pd.Series(val)
    med = vs.shift(1).rolling(60, min_periods=20).median().to_numpy()
    vm = vs.rolling(60, min_periods=20).median().to_numpy()
    with np.errstate(divide='ignore', invalid='ignore'):
        vx = val / med
        chg = (cc / pc - 1) * 100
    out = []
    for i, k1, k2 in wsig(c, m, cc):
        if True:
            if not (vm[i] >= MIN_VAL):
                continue
            b = None
            for j in range(i, min(n, i + 1 + WAIT)):
                if chg[j] >= CHG and vx[j] >= VX:
                    b = j
                    break
            f = cc[i + 1:i + 61]
            mx = mxn = None
            if len(f):
                k = int(np.nanargmax(f))
                mx, mxn = (f[k] / cc[i] - 1) * 100, k + 1
            out.append(dict(date=dates[i], close=cc[i], t1=dates[k1], t2=dates[k2],
                            big=dates[b] if b is not None else None,
                            bigchg=chg[b] if b is not None else None, bigvx=vx[b] if b is not None else None,
                            mx=mx, mxn=mxn, days=len(f), now=(cc[-1] / cc[i] - 1) * 100))
    return out


def signals(store):
    t, _ = flowsig.table(store)
    if t.empty:
        return pd.DataFrame(columns=COLS)
    key = (str(getattr(store, 'root', '')), len(t), t['date'].max())
    if key in _CACHE:
        return _CACHE[key]
    out = []
    for sym, x in t.sort_values(['sym', 'date']).groupby('sym'):
        if len(x) < 60:
            continue
        for r in _one(x['date'].to_numpy(), x['hi'].astype(float).to_numpy(), x['lo'].astype(float).to_numpy(),
                      x['last'].astype(float).to_numpy(), x['val_m'].astype(float).to_numpy() * 1e6):
            r['sym'] = sym
            out.append(r)
    r = pd.DataFrame(out, columns=COLS)
    _CACHE.clear()
    _CACHE[key] = r
    return r
