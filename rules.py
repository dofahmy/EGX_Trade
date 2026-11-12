# -*- coding: utf-8 -*-
"""القواعد اللي ثبتت في الاختبار (سبتمبر ٢٠٢٥ لنوفمبر ٢٠٢٦)، في صفحة واحدة.

شرا:
١. ذعر الصبح: ٤٠ سهم أو أكتر نازلين ٢٪ لحد ١٠:١٥ (قاع السوق).
٢. السهم النايم القريب من قمته.
قمة:
٣. قمة الانفجار، ومنطقة الخطر بعدها لحد ما السهم يقفل فوق أعلى سعر يوم الانفجار.
تحذيرات:
٤. الكبار بيبيعوا (أسبوعي، من bigsell).
٥. سهم نازل وجاله يوم كمية وصفقات ضخمة وهو بيقع لوحده.
"""
import numpy as np
import pandas as pd
import flowsig
import bigsell

MIN_VAL = 1e6
PANIC = 40
PANIC_SOFT = 20


def _feat(store):
    t, m = flowsig.table(store)
    if t.empty:
        return t, m
    t = t.sort_values(['sym', 'date']).reset_index(drop=True).copy()
    g = t.groupby('sym')
    t['val'] = t['val_m'] * 1e6
    t['vmed'] = g['vol'].transform(lambda v: v.shift(1).rolling(60, min_periods=30).median())
    t['nmed'] = g['n'].transform(lambda v: v.shift(1).rolling(60, min_periods=30).median())
    t['x'] = t['vol'] / t['vmed']
    t['xn'] = t['n'] / t['nmed']
    t['r20'] = (t['prev'] / g['last'].shift(21) - 1) * 100
    t['dhi'] = (t['hi'] / t['prev'] - 1) * 100
    t['clv'] = np.where(t['hi'] > t['lo'], (t['last'] - t['lo']) / (t['hi'] - t['lo']), 0.5)
    t['pmbs'] = t['pmb'] / (t['pmb'] + t['pms'])
    # النوم: آخر ١٠ جلسات قصاد المعتاد قبلهم (٦٠ جلسة)
    t['vmed_s'] = g['vol'].transform(lambda v: v.shift(10).rolling(60, min_periods=40).median())
    t['valmed_s'] = g['val'].transform(lambda v: v.shift(10).rolling(60, min_periods=40).median())
    t['dry'] = g['vol'].transform(lambda v: v.rolling(10, min_periods=7).mean()) / t['vmed_s']
    t['ret10'] = (t['last'] / g['last'].shift(10) - 1) * 100
    t['hi60'] = g['last'].transform(lambda v: v.rolling(60, min_periods=40).max())
    t['dd60'] = (t['last'] / t['hi60'] - 1) * 100
    t['liq'] = (t['med'] >= MIN_VAL) & ~t['thin'] & (t['gap'] <= 0.2)
    t['bo'] = t['liq'] & (t['r20'] >= 20) & (t['x'] >= 3) & (t['xn'] >= 3) & (t['dhi'] >= 5) & (t['clv'] <= 0.5) & (t['pmbs'] <= 0.5)
    t['sleep'] = (t['valmed_s'] >= 5e5) & ~t['thin'] & (t['dry'] <= 0.8) & (t['ret10'].abs() <= 5) & (t['dd60'] >= -6)
    t['dvol'] = t['liq'] & (t['r20'] <= -10) & (t['x'] >= 3) & (t['xn'] >= 3) & (t['chg'] < 0) & (t['date'].map(m['ret']) * 100 > -2)
    t['d15'] = t['liq'] & t['p15'].notna() & ((t['p15'] / t['prev'] - 1) * 100 <= -2)
    return t, m


def page(store, day=None):
    t, m = _feat(store)
    if t.empty:
        return {}
    dates = sorted(t['date'].unique())
    day = day if day in dates else dates[-1]
    i = dates.index(day)
    d = t[t['date'] == day]
    f = lambda v, k=1: None if pd.isna(v) else round(float(v), k)
    nm = lambda r: str(r.name) if isinstance(r.name, str) else ''

    # ١. ذعر الصبح
    cnt = t[t['d15']].groupby('date').size().reindex(dates).fillna(0).astype(int)
    idx = (1 + m['ret'].reindex(dates).fillna(0)).cumprod()
    panic = []
    for k in [x for x in dates if cnt[x] >= PANIC_SOFT][::-1][:15]:
        j = dates.index(k)
        n5 = (idx.iloc[j + 5] / idx.iloc[j] - 1) * 100 if j + 5 < len(dates) else None
        panic.append(dict(date=k, n=int(cnt[k]), mret=f(m.at[k, 'ret'] * 100), n5=f(n5), strong=cnt[k] >= PANIC))
    today_panic = dict(n=int(cnt[day]), strong=cnt[day] >= PANIC, soft=cnt[day] >= PANIC_SOFT,
                       has15=bool(d['p15'].notna().any()))

    # ٢. النايمين
    sl = d[d['sleep']].sort_values('dd60', ascending=False)
    sleep = [dict(sym=r.sym, name=nm(r), c=f(r.last, 3), dd=f(r.dd60), dry=f(r.dry, 2), r10=f(r.ret10)) for r in sl.itertuples()]

    # ٣. قمة الانفجار النهارده، ومنطقة الخطر (آخر ٢٠ جلسة لسه ماقفلش فوق أعلى سعر يوم الانفجار)
    lo = dates[max(0, i - 20)]
    ev = t[t['bo'] & (t['date'] >= lo) & (t['date'] <= day)]
    zone = []
    for r in ev.sort_values('date', ascending=False).itertuples():
        s = t[(t['sym'] == r.sym) & (t['date'] > r.date) & (t['date'] <= day)]
        if (s['last'] > r.hi).any():
            continue
        if any(z['sym'] == r.sym for z in zone):
            continue
        cur = s['last'].iloc[-1] if len(s) else r.last
        zone.append(dict(sym=r.sym, name=nm(r), date=r.date, today=r.date == day, hi=f(r.hi, 3), c=f(cur, 3),
                         below=f((cur / r.hi - 1) * 100), r20=f(r.r20), x=f(r.x), days=int(len(s))))

    # ٤. الكبار بيبيعوا: آخر أسبوع لحد اليوم ده
    try:
        w, _ = bigsell.weeks(store)
        wk = pd.Period(day, freq='W-THU')
        z = w[(w['wk'] == wk) & w['sig']].sort_values('mult', ascending=False)
        bigs = dict(start=str(wk.start_time.date()), end=str(wk.end_time.date()),
                    rows=[dict(sym=r.sym, name=nm(r), mult=f(r.mult), bnr=f(r.bnr), ret=f(r.ret)) for r in z.itertuples()])
    except Exception:
        bigs = dict(rows=[])

    # ٥. نازل + هوجة وهو بيقع لوحده: آخر ٥ جلسات
    lo5 = dates[max(0, i - 4)]
    dv = t[t['dvol'] & (t['date'] >= lo5) & (t['date'] <= day)].sort_values(['date', 'x'], ascending=[False, False])
    dvol = [dict(sym=r.sym, name=nm(r), date=r.date, r20=f(r.r20), x=f(r.x), xn=f(r.xn), chg=f(r.chg)) for r in dv.itertuples()]

    return dict(day=day, dates=dates[::-1][:120], mret=f(m.at[day, 'ret'] * 100) if day in m.index else None,
                panic_today=today_panic, panic=panic, sleep=sleep, zone=zone, bigs=bigs, dvol=dvol)
