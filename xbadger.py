# -*- coding: utf-8 -*-
"""أسهم-أكس-باجر.
الإشارة (السهم نايم وبيبدأ يصحى)، على مؤشر CCI طول ٢٠ على (أعلى + أوطى + إقفال) ÷ ٣، ومتوسطه البسيط ١٤:
  - المتوسط (الخط الأصفر) يلف لفوق وهو بين −١١٠ و−٥٠، وCCI (الأزرق) فوقه.
  - الإقفال قريب من متوسط آخر ٢٠ جلسة: الفرق ٥٪ أو أقل.
  - تجميع: أعلى إقفال في آخر ٦٠ جلسة مش أكتر من أوطى إقفال بـ٤٠٪.
  - قيمة التداول يوم الإشارة المعتاد أو أقل (وسيط آخر ٦٠ جلسة).
التأكيد: في خلال ١٠ جلسات بعد الإشارة السهم يطلع ٨٪ أو أكتر بقيمة تداول ٥ أضعاف المعتاد أو أكتر.
الأسعار من سيجما متعدلة (نفس الداتا اللي القاعدة اتختبرت عليها). والمدى بيحسب إقفال امبارح جواه زي تريدنج فيو.
"""
import numpy as np
import pandas as pd
import flowsig

M_LO, M_HI = -110.0, -50.0
NEAR = 5.0
RNG = 40.0
WAIT = 10
CHG = 8.0
VX = 5.0
MIN_VAL = 5e5
_CACHE = {}
COLS = ['sym', 'date', 'close', 'cci', 'm', 'brk', 'bchg', 'bvx', 'left']


def _mad(v):
    return np.mean(np.abs(v - v.mean()))


def signals(store):
    """كل إشارات أكس-باجر: brk = يوم التأكيد (أو None)، left = الجلسات الباقية في فترة المتابعة."""
    t, _ = flowsig.table(store)
    if t.empty:
        return pd.DataFrame(columns=COLS)
    key = (str(getattr(store, 'root', '')), len(t), t['date'].max())
    if key in _CACHE:
        return _CACHE[key]
    t = t[['sym', 'date', 'hi', 'lo', 'last', 'val_m']].copy()
    out = []
    for sym, x in t.sort_values(['sym', 'date']).groupby('sym'):
        if len(x) < 60:
            continue
        c = x['last'].astype(float).reset_index(drop=True)
        pc = c.shift(1)
        hi = np.fmax(x['hi'].astype(float).to_numpy(), pc.to_numpy())
        lo = np.fmin(x['lo'].astype(float).to_numpy(), pc.to_numpy())
        src = pd.Series((hi + lo + c.to_numpy()) / 3)
        dev = src.rolling(20).apply(_mad, raw=True).replace(0, np.nan)
        cci = ((src - src.rolling(20).mean()) / (0.015 * dev)).to_numpy()
        m = pd.Series(cci).rolling(14).mean().to_numpy()
        cc = c.to_numpy()
        s20 = (cc / c.rolling(20).mean().to_numpy() - 1) * 100
        rng = (c.rolling(60, min_periods=40).max() / c.rolling(60, min_periods=40).min() - 1).to_numpy() * 100
        val = pd.Series(x['val_m'].astype(float).to_numpy() * 1e6)
        med = val.shift(1).rolling(60, min_periods=20).median().to_numpy()
        vm20 = val.rolling(60, min_periods=20).median().to_numpy()
        with np.errstate(divide="ignore", invalid="ignore"):
            vx = val.to_numpy() / med
        chg = (cc / pc.to_numpy() - 1) * 100
        dates = x['date'].to_numpy()
        n = len(cc)
        for i in range(2, n):
            if not (np.isfinite(m[i]) and np.isfinite(m[i - 2]) and np.isfinite(cci[i])):
                continue
            if not (m[i] > m[i - 1] and m[i - 1] <= m[i - 2] and M_LO <= m[i] <= M_HI and cci[i] > m[i]):
                continue
            if not (abs(s20[i]) <= NEAR and rng[i] <= RNG and vx[i] <= 1 and vm20[i] >= MIN_VAL):
                continue
            b = None
            for j in range(i + 1, min(i + 1 + WAIT, n)):
                if chg[j] >= CHG and vx[j] >= VX:
                    b = j
                    break
            out.append(dict(sym=sym, date=dates[i], close=cc[i], cci=cci[i], m=m[i],
                            brk=dates[b] if b is not None else None,
                            bchg=chg[b] if b is not None else None,
                            bvx=vx[b] if b is not None else None,
                            left=max(0, WAIT - (n - 1 - i))))
    r = pd.DataFrame(out, columns=COLS)
    _CACHE.clear()
    _CACHE[key] = r
    return r
