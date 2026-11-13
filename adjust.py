# -*- coding: utf-8 -*-
"""تعديل الأسعار بعد الإجراءات على الأسهم (أسهم مجانية، تجزئة، تجميع).

سيجما بيدّي الأسعار زي ما اتنفذت، فيوم الإجراء السعر بيقع أو يقفز فجأة (زي AMES يوم ٢٨ أبريل ٢٠٢٦: من ٦٢٫٥٢ لـ٣٨٫٢٠).
بنعرف يوم الإجراء كده: الفتح بعيد عن إقفال امبارح بـ٢١٪ أو أكتر (أكبر من الحد اليومي)،
وإقفال اليوم وإقفال الجلسة اللي بعده لسه بعاد عن إقفال امبارح بـ١٥٪ أو أكتر في نفس الاتجاه (يعني مش غلطة سعر ورجعت).
نسبة التعديل = الفتح ÷ إقفال امبارح، وكل الأسعار اللي قبل يوم الإجراء بتتضرب فيها، والكميات بتتقسم عليها.

ملف actions.json (جنب ملفات التطبيق) بيسمح بالتصحيح بالإيد:
{"AMES": {"2026-04-28": 0.611}, "XXXX": {"2026-01-01": null}}   (null = مش إجراء، تجاهله)
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd

GAP = 0.21
STAY = 0.15
PRICE = ('open', 'hi', 'lo', 'last', 'prev', 'p11', 'p12', 'p15', 'c')
VOLS = ('vol', 'h1vol', 'h1b', 'h1s', 'pmb', 'pms')


def manual(root):
    """التصحيحات بالإيد: actions.json جنب ملفات التطبيق (بيترفع مع جيت هب)، أو في فولدر الداتا."""
    out = {}
    for p in (Path(__file__).resolve().parent / 'actions.json', Path(root) / 'actions.json' if root else None):
        try:
            for sym, days in json.loads(p.read_text(encoding='utf-8')).items():
                out.setdefault(sym, {}).update(days)
        except Exception:
            pass
    return out


def detect(t, root=None):
    """يرجّع جدول: sym, date, factor."""
    if t.empty or 'open' not in t or 'prev' not in t:
        return pd.DataFrame(columns=['sym', 'date', 'factor'])
    s = t[['sym', 'date', 'open', 'last', 'prev']].sort_values(['sym', 'date']).copy()
    s['nlast'] = s.groupby('sym')['last'].shift(-1)
    gap = s['open'] / s['prev'] - 1
    r0 = s['last'] / s['prev'] - 1
    r1 = s['nlast'] / s['prev'] - 1
    dn = (gap <= -GAP) & (r0 <= -STAY) & (r1 <= -STAY)
    up = (gap >= GAP) & (r0 >= STAY) & (r1 >= STAY)
    a = s[(dn | up) & (s['prev'] > 0)].copy()
    a['factor'] = a['open'] / a['prev']
    a = a[['sym', 'date', 'factor']]
    for sym, days in manual(root).items():
        for d, fac in days.items():
            a = a[~((a['sym'] == sym) & (a['date'] == d))]
            if fac:
                a = pd.concat([a, pd.DataFrame([{'sym': sym, 'date': d, 'factor': float(fac)}])], ignore_index=True)
    return a.sort_values(['date', 'sym']).reset_index(drop=True)


def apply(t, acts):
    """يعدّل الأسعار والكميات في جدول فيه sym وdate (بيرجّع نسخة)."""
    if t.empty or acts.empty:
        return t
    t = t.sort_values(['sym', 'date']).reset_index(drop=True).copy()
    cum = np.ones(len(t))
    for sym, a in acts.groupby('sym'):
        idx = np.where(t['sym'].values == sym)[0]
        if not len(idx):
            continue
        dates = t['date'].values[idx]
        for d, fac in zip(a['date'], a['factor']):
            cum[idx[dates < d]] *= fac
    t['adj'] = cum
    for c in PRICE:
        if c in t:
            t[c] = t[c] * cum
    for c in VOLS:
        if c in t:
            t[c] = t[c] / cum
    # يوم الإجراء نفسه: إقفال امبارح اتعدل بنسبة اليوم اللي قبله، فبنحسبه من جديد من الإقفال المتعدل
    if 'last' in t and 'prev' in t:
        g = t.groupby('sym')
        pl = g['last'].shift(1)
        hit = t['adj'].values != g['adj'].shift(1).fillna(t['adj']).values
        t.loc[hit, 'prev'] = pl[hit]
        if 'chg' in t:
            t.loc[hit, 'chg'] = (t.loc[hit, 'last'] / t.loc[hit, 'prev'] - 1) * 100
    return t
