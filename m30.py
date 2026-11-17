# -*- coding: utf-8 -*-
"""أول نص ساعة من الجلسة (١٠:٠٠ لحد ١٠:٣٠) من تنفيذات سيجما.

- البرنامج اللي على الجهاز بيرفع تنفيذات اليوم بعد ١٠:٣٠ (نوع m30).
- هنا بنحتفظ بتنفيذات أول نص ساعة بس، ونحسب لكل سهم:
  التغير عن إقفال امبارح، وقيمة التداول، ونصيب المشتري (الصفقة بسعر أعلى من اللي قبلها = شراء،
  بسعر أقل = بيع، ونفس السعر = زي اللي قبلها)، والكمية مقارنة بكمية امبارح كلها.
- إقفال امبارح وكميته والسيولة من داتا تريدنج فيو اليومية.
- بنكتب ملخص بالعربي. لو فيه مفتاح ANTHROPIC_API_KEY في ريلواي، النموذج بيكتب التعليق من الأرقام دي.
"""
import json
import os
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

import tvdaily

END = 10 * 60 + 30        # ١٠:٣٠
MIN_LIQ = 5e5             # متوسط سيولة يومية ٥٠٠ ألف جنيه
MODEL = os.environ.get('M30_MODEL', 'claude-haiku-5-5')


def folder(root):
    p = Path(root) / 'm30'
    p.mkdir(parents=True, exist_ok=True)
    return p


def _mins(t):
    t = pd.to_datetime(t.astype(str).str.strip(), format='%I:%M:%S %p', errors='coerce')
    return t.dt.hour * 60 + t.dt.minute


def save(root, date, csv_path):
    """بيحفظ تنفيذات أول نص ساعة بس."""
    d = pd.read_csv(csv_path, dtype={'Symbol': str})
    need = {'Symbol', 'Time', 'Volume', 'Price', 'Value'}
    if not need.issubset(d.columns):
        raise ValueError('الملف مش ملف تنفيذات سيجما')
    if 'Ticket No.' in d:
        d = d.drop_duplicates(['Symbol', 'Ticket No.']).sort_values(['Symbol', 'Ticket No.'])
    d = d[_mins(d['Time']) < END]
    cols = [c for c in ['Symbol', 'Time', 'Ticket No.', 'Volume', 'Price', 'Value'] if c in d]
    d[cols].to_csv(folder(root) / f'{date}.csv', index=False)
    return len(d)


def try_from(root, date, csv_path):
    """من أي رفع لتنفيذات اليوم (مش لازم يكون نوع m30): لو الصفقات وصلت لـ١٠:٣٠، نعمل الملخص.
    لو الملخص بتاع اليوم ده اتعمل قبل كده، مانعيدهوش."""
    if (folder(root) / f'{date}.json').exists():
        return False
    d = pd.read_csv(csv_path, dtype={'Symbol': str}, usecols=lambda c: c in ('Symbol', 'Time'))
    m = _mins(d['Time'])
    if not len(m) or m.max() < END - 1:
        return False
    save(root, date, csv_path)
    return build(root, date) is not None


def _daily(root, date):
    """من داتا تريدنج فيو: إقفال وكمية آخر يوم قبل التاريخ، ومتوسط السيولة في ٢٠ يوم."""
    t = tvdaily.load(root)
    if t is None or not len(t):
        return pd.DataFrame(columns=['pc', 'pv', 'liq'])
    t = t[t['date'].astype(str) < str(date)].sort_values(['sym', 'date'])
    t = t.assign(v=t['close'] * t['volume'])
    g = t.groupby('sym')
    last = g.tail(1).set_index('sym')
    liq = g.tail(20).groupby('sym')['v'].median()
    return pd.DataFrame({'pc': last['close'], 'pv': last['volume'], 'liq': liq})


def analyze(root, date):
    p = folder(root) / f'{date}.csv'
    if not p.exists():
        return None
    d = pd.read_csv(p, dtype={'Symbol': str})
    if not len(d):
        return None
    g = d.groupby('Symbol', sort=False)
    s = np.sign(g['Price'].diff()).replace(0, np.nan)
    d = d.assign(s=s.groupby(d['Symbol']).ffill().fillna(0))
    a = pd.DataFrame({
        'val': g['Value'].sum(), 'vol': g['Volume'].sum(),
        'o': g['Price'].first(), 'p': g['Price'].last(), 'n': g.size(),
        'bv': d[d.s > 0].groupby('Symbol')['Value'].sum(),
        'sv': d[d.s < 0].groupby('Symbol')['Value'].sum(),
    }).fillna({'bv': 0, 'sv': 0})
    a = a.join(_daily(root, date), how='inner')
    a = a[(a.pc > 0) & (a.liq >= MIN_LIQ) & ((a.o / a.pc - 1).abs() <= 0.2)]
    if not len(a):
        return None
    a['chg'] = (a.p / a.pc - 1) * 100
    a['bsh'] = a.bv / (a.bv + a.sv).replace(0, np.nan)
    a['x'] = a.vol / a.pv.replace(0, np.nan)

    up, dn = int((a.chg > 0.05).sum()), int((a.chg < -0.05).sum())
    flat = len(a) - up - dn
    tot = float(a.val.sum())
    mb = float(a.bv.sum() / max(1.0, (a.bv + a.sv).sum()))
    avg = float(a.chg.mean())

    # مقارنة قيمة التداول بأول نص ساعة في آخر جلسة متسجلة
    prev_tot = None
    olds = sorted(x for x in folder(root).glob('*.csv') if x.stem < str(date))
    if olds:
        try:
            q = pd.read_csv(olds[-1], dtype={'Symbol': str})
            prev_tot = float(q[q['Symbol'].isin(a.index)]['Value'].sum())
        except Exception:
            prev_tot = None

    big = a[a.val >= max(1e6, a.val.quantile(0.5))]
    buy = big[big.bsh >= 0.6].sort_values('val', ascending=False).head(6)
    sell = big[big.bsh <= 0.4].sort_values('val', ascending=False).head(6)
    act = a[(a.x >= 1) & (a.val >= 1e6)].sort_values('x', ascending=False).head(6)
    top = a.sort_values('val', ascending=False).head(5)

    def rows(x):
        return [dict(s=i, chg=round(float(r.chg), 1), val=round(float(r.val) / 1e6, 1),
                     bsh=None if pd.isna(r.bsh) else int(round(r.bsh * 100)),
                     x=None if pd.isna(r.x) else round(float(r.x), 1), p=float(r.p))
                for i, r in x.iterrows()]

    tone = 'buy' if (mb >= 0.55 and up > dn) else 'sell' if (mb <= 0.45 and dn > up) else 'mix'
    return dict(date=str(date), n=len(a), up=up, dn=dn, flat=flat, tot=round(tot / 1e6, 1),
                prev_tot=None if prev_tot is None else round(prev_tot / 1e6, 1),
                mb=int(round(mb * 100)), avg=round(avg, 2), tone=tone,
                buy=rows(buy), sell=rows(sell), act=rows(act), top=rows(top))


def _ar(v):
    s = str(v)
    return s.translate(str.maketrans('0123456789.-', '٠١٢٣٤٥٦٧٨٩٫−'))


def _pct(v):
    return ('+' if v > 0 else '') + _ar(v) + '٪'


def text(r):
    """ملخص مكتوب من الأرقام (من غير نموذج)."""
    if not r:
        return []
    tone = {'buy': 'بميل شرائي', 'sell': 'بميل بيعي', 'mix': 'بأداء متوازن بين المشترين والبائعين'}[r['tone']]
    out = [f"بدأت جلسة اليوم {tone}؛ ارتفع {_ar(r['up'])} سهمًا مقابل تراجع {_ar(r['dn'])}، "
           f"وكانت {_ar(r['mb'])}٪ من قيمة التداول صفقات شراء، أي صفقات نُفّذت بسعر أعلى من الصفقة التي قبلها."]
    s = f"بلغت قيمة التداول في أول نصف ساعة {_ar(r['tot'])} مليون جنيه"
    if r.get('prev_tot'):
        ch = (r['tot'] / r['prev_tot'] - 1) * 100
        if abs(ch) >= 5:
            s += f"، {'أعلى' if ch > 0 else 'أقل'} من أول نصف ساعة في الجلسة السابقة بنحو {_ar(round(abs(ch)))}٪"
    out.append(s + '.')
    if r['top']:
        out.append('الأعلى تداولًا: ' + '، '.join(f"{x['s']} ({_pct(x['chg'])})" for x in r['top']) + '.')
    return out


def _ai(r, base):
    key = os.environ.get('ANTHROPIC_API_KEY', '')
    if not key:
        return None
    prompt = (
        'أنت محلل لسوق الأسهم المصرية. هذه أرقام أول نصف ساعة من جلسة اليوم (من ١٠:٠٠ إلى ١٠:٣٠)، '
        'محسوبة من تنفيذات الصفقات. نسبة المشتري = نصيب الصفقات المنفذة بسعر أعلى من الصفقة السابقة من قيمة التداول. '
        'x = كمية أول نصف ساعة مقسومة على كمية جلسة أمس كاملة.\n'
        f'{json.dumps(r, ensure_ascii=False)}\n\n'
        'اكتب تعليقًا قصيرًا بالعربية الفصحى السليمة في ٤ إلى ٦ جمل: صف اتجاه بداية الجلسة، وأين يسيطر المشترون، '
        'وأين يسيطر البائعون، وأي نشاط غير معتاد. اذكر رموز الأسهم كما هي. '
        'لا تقدم أي توصية بالشراء أو البيع ولا توقعات للأسعار، ولا تضف أرقامًا غير موجودة. '
        'اكتب النص فقط بلا عناوين ولا نقاط.')
    body = json.dumps({'model': MODEL, 'max_tokens': 600,
                       'messages': [{'role': 'user', 'content': prompt}]}).encode()
    req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=body, headers={
        'x-api-key': key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as f:
            j = json.loads(f.read())
        t = ''.join(c.get('text', '') for c in j.get('content', []) if c.get('type') == 'text').strip()
        return [x.strip() for x in t.split('\n') if x.strip()] or None
    except Exception as e:
        print('[أول نص ساعة] النموذج ماردش:', str(e)[:200], flush=True)
        return None


def build(root, date):
    """بيحسب ويكتب الملخص ويحفظه، بيتنادى مرة واحدة بعد الرفع."""
    r = analyze(root, date)
    if not r:
        return None
    base = text(r)
    ai = _ai(r, base)
    r['lines'] = ai or base
    r['by_ai'] = bool(ai)
    (folder(root) / f'{date}.json').write_text(json.dumps(r, ensure_ascii=False), encoding='utf-8')
    return r


def latest(root):
    fs = sorted(folder(root).glob('*.json'))
    if not fs:
        return None
    try:
        r = json.loads(fs[-1].read_text(encoding='utf-8'))
        if not r.get('by_ai'):
            r['lines'] = text(r)   # الصياغة دايمًا بآخر نسخة
        return r
    except Exception:
        return None
