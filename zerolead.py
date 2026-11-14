# -*- coding: utf-8 -*-
"""إشارات "المؤشر سابق السعر" على CCI ٤٠ (الأصفر متوسط بسيط ٢٨)، على شموع تريدنج فيو اليومية كلها.
الإشارة:
  - الأزرق فضل تحت الصفر ٣٠ جلسة أو أكتر من آخر مرة كان فوقه، والسعر نزل ٢٠٪ أو أكتر من قمته في الفترة دي.
  - أول يوم الأزرق يعدي الصفر، وإقفاله لسه تحت إقفال يوم أعلى قمة عملها الأزرق بعد قاعه (المؤشر سابق السعر).
  - لو شمعة الإشارة حمرا: الشرا بيتفعّل لما المؤشر والإقفال يعدّوا قيمتهم يوم الإشارة (في خلال ١٠ جلسات).
الفلاتر: السوق كله (متوسط كل الأسهم) نازل ١٥٪+ من أعلى نقطة في سنة، والسهم نازل ٣٥٪+، والأصفر مش أوطى من −٩٨،
والسعر ١٠ قروش أو أكتر، ووسيط قيمة التداول ٥٠٠ ألف جنيه أو أكتر.
"""
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view as swv

import tvdaily

L, M = 40, 28
BELOW, FALL, W, WAITX = 30, 20.0, 3, 10
F_MKT, F_FALL, F_YEL, F_PX, F_VAL = -15.0, -35.0, -98.0, 0.1, 5e5
_CACHE = {}


def cci(hi, lo, cl):
    src = (hi + lo + cl) / 3
    n = len(src)
    k = np.full(n, np.nan)
    if n >= L:
        win = swv(src, L)
        mu = win.mean(axis=1)
        mad = np.abs(win - mu[:, None]).mean(axis=1)
        with np.errstate(divide='ignore', invalid='ignore'):
            k[L - 1:] = (src[L - 1:] - mu) / (0.015 * mad)
    k[~np.isfinite(k)] = np.nan
    m = pd.Series(k).rolling(M).mean().to_numpy()
    return k, m


def _one(dates, o, hi, lo, c, val, mdd):
    k, m = cci(hi, lo, c)
    n = len(c)
    vmed = pd.Series(val).rolling(20, min_periods=10).median().to_numpy()
    out = []
    last_pos = None
    for i in range(1, n):
        if not np.isfinite(k[i]):
            continue
        if k[i] > 0 and k[i - 1] <= 0 and last_pos is not None and i - last_pos - 1 >= BELOW:
            a = last_pos
            top = np.nanmax(c[max(0, a - 10):i])
            fall = (np.nanmin(c[a:i]) / top - 1) * 100
            kk = k[a + 1:i]
            if len(kk) and np.isfinite(kk).any():
                kmin = a + 1 + int(np.nanargmin(kk))
                peaks = [j for j in range(kmin + 1, i - 1)
                         if np.isfinite(k[j]) and k[j] == np.nanmax(k[max(kmin, j - W):min(i, j + W + 1)]) and k[j] > k[kmin] + 30]
                pk = max(peaks, key=lambda j: k[j]) if peaks else None
                if fall <= -FALL and pk is not None and c[i] < c[pk]:
                    green = c[i] >= o[i]
                    buy = i if green else None
                    if not green:
                        for q in range(i + 1, min(n, i + 1 + WAITX)):
                            if k[q] > k[i] and c[q] > c[i]:
                                buy = q
                                break
                    r = dict(d=dates[i], c=round(float(c[i]), 3), b=int(i - a - 1), fa=round(float(fall), 1),
                             pk=dates[pk], kp=round(float(k[pk]), 1), cp=round(float(c[pk]), 3), g=bool(green),
                             ym=round(float(m[i]), 1) if np.isfinite(m[i]) else None,
                             md=round(float(mdd[i]), 1) if np.isfinite(mdd[i]) else None,
                             liq=bool(vmed[i] >= F_VAL) if np.isfinite(vmed[i]) else False,
                             bd=None, bp=None, mx=None, n=0)
                    if buy is not None:
                        f = c[buy + 1:buy + 121]
                        r.update(bd=dates[buy], bp=round(float(c[buy]), 3), n=int(len(f)))
                        if len(f):
                            r['mx'] = round(float((np.nanmax(f) / c[buy] - 1) * 100), 1)
                    r['ok'] = bool(r['liq'] and c[i] >= F_PX and r['md'] is not None and r['md'] <= F_MKT
                                   and fall <= F_FALL and r['ym'] is not None and r['ym'] > F_YEL)
                    out.append(r)
        if k[i] > 0:
            last_pos = i
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
    mdd = ((idx / idx.rolling(250, min_periods=50).max() - 1) * 100)
    out = []
    for sym, z in d.groupby('sym'):
        if len(z) < 200:
            continue
        dates = z['date'].to_numpy()
        c = z['close'].astype(float).to_numpy()
        val = c * z['volume'].astype(float).to_numpy()
        for r in _one(dates, z['open'].astype(float).to_numpy(), z['high'].astype(float).to_numpy(),
                      z['low'].astype(float).to_numpy(), c, val, mdd.reindex(dates).to_numpy()):
            r['s'] = sym
            out.append(r)
    out.sort(key=lambda r: r['d'], reverse=True)
    _CACHE.clear()
    _CACHE[key] = out
    return out
