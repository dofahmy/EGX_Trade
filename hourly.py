# -*- coding: utf-8 -*-
"""قراءة الجلسة كل ساعة (تجربة، صفحة خاصة).

نقاط القراءة: ١٠:٣٠ ثم ١١:٣٠ ثم ١٢:٣٠ ثم ١:٣٠ ثم ٢:٣٠ (الإغلاق).
- كل رفع لصفقات يوم (أول نص ساعة، أو كل ساعة، أو آخر اليوم) بيتحفظ هنا، ونحسب كل نقطة اكتملت.
- لكل نقطة: أرقام الساعة نفسها (من النقطة اللي قبلها لحد النقطة دي)، وأرقام اليوم لحد النقطة.
- الربط: مقارنة بالساعة اللي قبلها، والأسهم اللي مستمر فيها الشراء أو البيع، والأسهم اللي انقلبت،
  ومقارنة بنفس التوقيت في الجلسة اللي قبلها.
- نصيب الشراء = نصيب الصفقات اللي اتنفذت بسعر أعلى من الصفقة اللي قبلها في نفس السهم، من قيمة التداول.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

import m30

START = 10 * 60
CPS = [(600, 630, '١٠:٣٠'), (630, 690, '١١:٣٠'), (690, 750, '١٢:٣٠'), (750, 810, '١:٣٠'), (810, 870, '٢:٣٠')]
KEEP = 45            # أيام بنحتفظ بصفقاتها
MINV = 3e5           # أقل قيمة تداول للسهم في الساعة عشان يدخل في قوائم السيطرة


def folder(root):
    p = Path(root) / 'hourly'
    p.mkdir(parents=True, exist_ok=True)
    return p


def ingest(root, date, csv_path):
    """بيحفظ صفقات اليوم (لو الملف الجديد فيه صفقات أكتر) وبيحسب النقط اللي اكتملت."""
    d = pd.read_csv(csv_path, dtype={'Symbol': str})
    need = {'Symbol', 'Time', 'Volume', 'Price', 'Value'}
    if not need.issubset(d.columns) or not len(d):
        return None
    cols = [c for c in ['Symbol', 'Time', 'Ticket No.', 'Volume', 'Price', 'Value'] if c in d]
    p = folder(root) / f'{date}.csv.gz'
    if p.exists():
        try:
            old = pd.read_csv(p, usecols=['Symbol'])
            if len(old) > len(d):
                return compute(root, date)
        except Exception:
            pass
    d[cols].to_csv(p, index=False, compression='gzip')
    olds = sorted(folder(root).glob('*.csv.gz'))
    for x in olds[:-KEEP]:
        x.unlink(missing_ok=True)
    return compute(root, date)


def _prev(root, date):
    fs = sorted(x for x in folder(root).glob('*.json') if x.stem < str(date))
    if not fs:
        return None
    try:
        return json.loads(fs[-1].read_text(encoding='utf-8'))
    except Exception:
        return None


def compute(root, date):
    p = folder(root) / f'{date}.csv.gz'
    if not p.exists():
        return None
    d = pd.read_csv(p, dtype={'Symbol': str})
    if 'Ticket No.' in d:
        d = d.drop_duplicates(['Symbol', 'Ticket No.']).sort_values(['Symbol', 'Ticket No.'])
    d['mn'] = m30._mins(d['Time'])
    d = d[d.mn.notna()]
    if not len(d):
        return None
    s = np.sign(d.groupby('Symbol')['Price'].diff()).replace(0, np.nan)
    d['s'] = s.groupby(d['Symbol']).ffill().fillna(0)
    d['bv'] = np.where(d.s > 0, d.Value, 0.0)
    d['sv'] = np.where(d.s < 0, d.Value, 0.0)

    ref = m30._daily(root, date)
    first = d.groupby('Symbol')['Price'].first()
    ref = ref.join(first.rename('o'), how='inner')
    ref = ref[(ref.pc > 0) & (ref.liq >= m30.MIN_LIQ) & ((ref.o / ref.pc - 1).abs() <= 0.2)]
    d = d[d.Symbol.isin(ref.index)]
    if not len(d):
        return None
    last_min = int(d.mn.max())
    prev_day = _prev(root, date)
    pcp = {c['t']: c for c in (prev_day or {}).get('cps', [])}

    out, hist = [], []
    for a, b, lab in CPS:
        if last_min < b - 1:
            break
        w = d[(d.mn >= a) & (d.mn < b)]
        cu = d[d.mn < b]
        if not len(w):
            continue
        g = w.groupby('Symbol')
        st = pd.DataFrame({'val': g.Value.sum(), 'bv': g.bv.sum(), 'sv': g.sv.sum()})
        st['bsh'] = st.bv / (st.bv + st.sv).replace(0, np.nan)
        lastp = cu.groupby('Symbol')['Price'].last()
        cvol = cu.groupby('Symbol')['Volume'].sum()
        z = ref.join(lastp.rename('p'), how='inner')
        z['chg'] = (z.p / z.pc - 1) * 100
        z['x'] = cvol.reindex(z.index) / z.pv.replace(0, np.nan)
        st = st.join(z[['chg', 'x', 'p']], how='left')

        up, dn = int((z.chg > 0.05).sum()), int((z.chg < -0.05).sum())
        wv, cv = float(st.val.sum()), float(cu.Value.sum())
        mb = float(st.bv.sum() / max(1.0, (st.bv + st.sv).sum()))
        cmb = float(cu.bv.sum() / max(1.0, (cu.bv + cu.sv).sum()))
        big = st[st.val >= MINV]
        buy = set(big[big.bsh >= 0.6].index)
        sell = set(big[big.bsh <= 0.4].index)

        def chip(ix, n=6, key='val'):
            q = st.loc[[i for i in ix if i in st.index]].sort_values(key, ascending=False).head(n)
            return [dict(s=i, chg=None if pd.isna(r.chg) else round(float(r.chg), 1),
                         val=round(float(r.val) / 1e6, 1),
                         bsh=None if pd.isna(r.bsh) else int(round(r.bsh * 100))) for i, r in q.iterrows()]

        c = dict(t=lab, a=a, b=b, up=up, dn=dn, wv=round(wv / 1e6, 1), cv=round(cv / 1e6, 1),
                 mb=int(round(mb * 100)), cmb=int(round(cmb * 100)),
                 buy=chip(buy), sell=chip(sell),
                 act=chip(set(st[(st.x >= 1) & (st.val >= MINV)].index), key='x') if len(st) else [])
        if hist:
            h = hist[-1]
            c['keep_b'] = chip(buy & h['buy'])
            c['keep_s'] = chip(sell & h['sell'])
            c['to_b'] = chip(buy & h['sell'])
            c['to_s'] = chip(sell & h['buy'])
            c['pw'] = out[-1]['wv']
            c['prate'] = out[-1]['wv'] / max(1, out[-1]['b'] - out[-1]['a'])
            c['pmb'] = out[-1]['mb']
            c['pup'], c['pdn'] = out[-1]['up'], out[-1]['dn']
        if all_b := (set.intersection(*[x['buy'] for x in hist], buy) if hist else set()):
            c['all_b'] = chip(all_b)
        if lab in pcp:
            y = pcp[lab]
            c['y'] = dict(cv=y['cv'], cmb=y['cmb'], up=y['up'], dn=y['dn'], date=prev_day['date'])
        c['lines'] = text(c, first=not hist)
        out.append(c)
        hist.append(dict(buy=buy, sell=sell))

    # مصفوفة الأسهم: نصيب الشراء في كل ساعة لأكبر الأسهم تداولًا
    mx = []
    if out:
        b_last = out[-1]['b']
        cu = d[d.mn < b_last]
        tot = cu.groupby('Symbol')['Value'].sum().sort_values(ascending=False)
        lastp = cu.groupby('Symbol')['Price'].last()
        for sym in tot.index[:60]:
            hs = []
            for c in out:
                w = d[(d.Symbol == sym) & (d.mn >= c['a']) & (d.mn < c['b'])]
                v = float(w.Value.sum())
                if v < MINV / 2:
                    hs.append(None)
                    continue
                bb, ss = float(w.bv.sum()), float(w.sv.sum())
                hs.append(None if bb + ss == 0 else int(round(bb / (bb + ss) * 100)))
            pc = float(ref.loc[sym, 'pc']) if sym in ref.index else None
            mx.append(dict(s=sym, val=round(float(tot[sym]) / 1e6, 1), h=hs,
                           chg=None if not pc else round((float(lastp[sym]) / pc - 1) * 100, 1)))
    r = dict(date=str(date), last=last_min, cps=out, mx=mx, prev=(prev_day or {}).get('date'))
    (folder(root) / f'{date}.json').write_text(json.dumps(r, ensure_ascii=False), encoding='utf-8')
    return r


A = m30._ar


def _names(xs):
    return '، '.join(x['s'] for x in xs)


def _pctchg(a, b):
    return None if not b else (a / b - 1) * 100


def text(c, first=False):
    L = []
    tone = 'شرائي' if c['mb'] >= 55 else 'بيعي' if c['mb'] <= 45 else 'متوازن'
    if first:
        L.append(f"أول نصف ساعة: الطابع {tone}، ونصيب الشراء {A(c['mb'])}٪ من قيمة تداول "
                 f"{A(c['wv'])} مليون جنيه؛ والأسهم المرتفعة {A(c['up'])} مقابل {A(c['dn'])} متراجعة.")
    else:
        rate = c['wv'] / max(1, c['b'] - c['a'])
        ch = _pctchg(rate, c.get('prate'))
        prv = 'أول نصف ساعة' if c['a'] == 630 else 'الساعة السابقة'
        dv = '' if ch is None or abs(ch) < 5 else (f"، بمعدل تداول {'أعلى' if ch > 0 else 'أقل'} من {prv} بنحو {A(round(abs(ch)))}٪")
        if ch is not None and abs(ch) < 5:
            dv = f"، بنفس معدل تداول {prv} تقريبًا"
        L.append(f"في هذه الساعة تداول السوق {A(c['wv'])} مليون جنيه{dv}، "
                 f"ونصيب الشراء {A(c['mb'])}٪ (كان {A(c['pmb'])}٪)، فالطابع {tone}.")
        L.append(f"الأسهم المرتفعة الآن {A(c['up'])} مقابل {A(c['dn'])} متراجعة "
                 f"(كانت {A(c['pup'])} مقابل {A(c['pdn'])}).")
        if c.get('all_b'):
            L.append('الشراء مسيطر منذ بداية الجلسة على: ' + _names(c['all_b']) + '.')
        elif c.get('keep_b'):
            L.append('استمر الشراء من الساعة السابقة في: ' + _names(c['keep_b']) + '.')
        if c.get('to_b'):
            L.append('انقلب من بيع إلى شراء: ' + _names(c['to_b']) + '.')
        if c.get('to_s'):
            L.append('انقلب من شراء إلى بيع: ' + _names(c['to_s']) + '.')
    y = c.get('y')
    if y:
        ch = _pctchg(c['cv'], y['cv'])
        s = f"مقارنة بنفس التوقيت في جلسة {A(y['date'][8:10])}\u200F/\u200F{A(y['date'][5:7])}: قيمة التداول حتى الآن {A(c['cv'])} مليون"
        if ch is not None:
            s += f" ({'أعلى' if ch > 0 else 'أقل'} بنحو {A(round(abs(ch)))}٪)"
        s += f"، ونصيب الشراء {A(c['cmb'])}٪ مقابل {A(y['cmb'])}٪."
        L.append(s)
    return L


def dates(root):
    return sorted((x.stem for x in folder(root).glob('*.json')), reverse=True)


def load(root, date):
    p = folder(root) / f'{date}.json'
    if not p.exists():
        return None
    r = json.loads(p.read_text(encoding='utf-8'))
    if 'mx' not in r:          # محسوب بنسخة قديمة: نعيد الحساب من الصفقات المحفوظة
        try:
            r = compute(root, date) or r
        except Exception:
            pass
    return r
