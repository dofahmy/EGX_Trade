# -*- coding: utf-8 -*-
"""إشارة الساعة الأولى، والإشارة على السوق كله، والحد الأقصى، لصفحة الشموع.

القواعد (نفس اللي اتختبرت على ١٣ شهر):
- إشارة مشتري في سهم: كمية الساعة الأولى ≥ كمية الجلسة اللي قبلها كلها، والمشتري ≥ ٦٠٪ من كمية الساعة الأولى،
  وقيمة تداول السهم المعتادة (وسيط آخر ٢٠ جلسة) ≥ مليون جنيه.
- إشارة بياع: نفس الشرط بس البياع ≥ ٦٠٪.
- يوم شراء على السوق: (عدد أسهم المشتري − عدد أسهم البياع) ≥ ١٠ في نفس اليوم.
- الحد الأقصى: السهم قفل طالع ١٩٫٥٪ أو أكتر.
"""
from pathlib import Path
import numpy as np
import adjust
import pandas as pd

MIN_STOCKS = 10
BUY_SHARE = 0.6
MIN_VAL = 1e6
LIMIT = 19.5

SEED = Path(__file__).resolve().parent / 'h1_seed.csv.gz'
_SEED = None
_CACHE = {}
ACTS = {}


def seed():
    """بيانات الساعة الأولى للجلسات القديمة اللي اترفعت قبل ما التطبيق يحسبها."""
    global _SEED
    if _SEED is None:
        _SEED = pd.read_csv(SEED, dtype={'sym': str}).set_index(['date', 'sym']) if SEED.exists() else pd.DataFrame()
    return _SEED


def table(store):
    """جدول كل الأسهم في كل الجلسات بالإشارات. بيتحسب مرة ويتخزن لحد ما جلسة تتغير."""
    dates = store.dates()
    try:
        stamp = tuple((d, (store.sdir(d) / 'stats.csv').stat().st_mtime) for d in dates)
    except Exception:
        stamp = None
    key = str(getattr(store, 'root', id(store)))
    if stamp is not None and key in _CACHE and _CACHE[key][0] == stamp:
        return _CACHE[key][1]
    res = _build(store, dates)
    if stamp is not None:
        _CACHE[key] = (stamp, res)
    return res


def _build(store, dates):
    sd = seed()
    parts = []
    for d in dates:
        try:
            st = pd.read_csv(store.sdir(d) / 'stats.csv', dtype={'sym': str}).set_index('sym')
        except Exception:
            continue
        cols = ['name', 'open', 'hi', 'lo', 'last', 'prev', 'chg', 'vol', 'val_m', 'n']
        x = st[[c for c in cols if c in st]].copy()
        for c in ('h1vol', 'h1b', 'h1s', 'p12', 'p11', 'h1val', 'p15', 'val15', 'pmb', 'pms'):
            x[c] = st[c] if c in st else np.nan
        if len(sd):
            try:
                s = sd.loc[d]
                for c in ('h1vol', 'h1b', 'h1s', 'p12', 'p11', 'h1val', 'p15', 'val15', 'pmb', 'pms'):
                    if c in s and x[c].isna().all():
                        x[c] = s[c].reindex(x.index)
            except KeyError:
                pass
        x['date'] = d
        parts.append(x.reset_index())
    if not parts:
        return pd.DataFrame(), pd.DataFrame()
    t = pd.concat(parts, ignore_index=True).sort_values(['sym', 'date']).reset_index(drop=True)
    # الأسعار متعدلة بالإجراءات على الأسهم (أسهم مجانية وتجزئة)، زي تريدنج فيو
    t = adjust.drop_bad(t, getattr(store, 'root', None))
    acts = adjust.detect(t, getattr(store, 'root', None))
    t = adjust.apply(t, acts)
    ACTS[str(getattr(store, 'root', ''))] = acts
    g = t.groupby('sym')
    t['pvol'] = g['vol'].shift(1)
    t['pdate'] = g['date'].shift(1)
    t['med'] = g['val_m'].transform(lambda v: v.shift(1).rolling(20, min_periods=10).median()) * 1e6
    t['bsh'] = t['h1b'] / (t['h1b'] + t['h1s'])
    t['gap'] = (t['open'] / t['prev'] - 1).abs()
    t['g12'] = (t['p12'] / t['prev'] - 1) * 100
    # جلسة ناقصة في الداتا (أسهم قليلة جدًا اتداولت) بتبوّظ مقارنة اليوم اللي بعدها، فبنستبعدهم
    cnt = t.groupby('date')['n'].sum() if 'n' in t else t.groupby('date').size()
    thin = cnt < 0.4 * cnt.rolling(20, min_periods=1).median()
    bad = set(cnt.index[thin])
    t['thin'] = t['date'].isin(bad) | t['pdate'].isin(bad)
    base = ~t['thin'] & (t['pvol'] > 0) & (t['h1vol'] >= t['pvol']) & (t['med'] >= MIN_VAL) & (t['gap'] <= 0.2) & t['bsh'].notna()
    t['buy'] = base & (t['bsh'] >= BUY_SHARE)
    t['sell'] = base & (t['bsh'] <= 1 - BUY_SHARE)
    t['limit'] = t['chg'] >= LIMIT
    t['next_chg'] = g['chg'].shift(-1)
    t['has_h1'] = t['h1vol'].notna()
    # السوق: عدد أسهم المشتري والبياع كل يوم، ومؤشر بسيط (متوسط تغير الأسهم السايلة)
    liq = t['med'] >= MIN_VAL
    m = pd.DataFrame({
        'nb': t[t['buy']].groupby('date').size(),
        'ns': t[t['sell']].groupby('date').size(),
        'ret': t[liq & (t['gap'] <= 0.2)].groupby('date')['chg'].mean() / 100,
        'h1': t.groupby('date')['has_h1'].any(),
    }).reindex(sorted(t['date'].unique()))
    m[['nb', 'ns']] = m[['nb', 'ns']].fillna(0).astype(int)
    m['ret'] = m['ret'].fillna(0)
    m['idx'] = (1 + m['ret']).cumprod() * 100
    m['dd'] = (m['idx'] / m['idx'].rolling(20, min_periods=1).max() - 1) * 100
    m['net'] = m['nb'] - m['ns']
    m['cluster'] = m['net'] >= MIN_STOCKS      # يوم شراء: (أسهم المشتري − أسهم البياع) ≥ ١٠
    # ترتيب السهم في يوم الإشارة (حسب الصعود لحد الساعة ١٢)
    t['cluster'] = t['date'].map(m['cluster']).fillna(False)
    rk = t[t['buy'] & t['cluster']].groupby('date')['g12'].rank(ascending=False, method='first')
    t['rank'] = rk.reindex(t.index)
    return t, m


def adj_close(store):
    """الإقفال المتعدل لكل (تاريخ، سهم)، عشان الصفحات التانية تستخدمه."""
    t, _ = table(store)
    if t.empty:
        return pd.Series(dtype=float)
    return t.set_index(['date', 'sym'])['last']


def stock(store, sym):
    t, m = table(store)
    if t.empty:
        return [], {}
    s = t[t['sym'] == sym]
    f = lambda v: None if pd.isna(v) else round(float(v), 4)
    out = []
    for r in s.itertuples():
        out.append(dict(
            d=r.date, o=f(r.open), h=f(r.hi), l=f(r.lo), c=f(r.last), chg=f(r.chg) or 0.0,
            buy=bool(r.buy), sell=bool(r.sell), bsh=f(r.bsh), h1x=f(r.h1vol / r.pvol) if r.pvol and r.pvol > 0 and pd.notna(r.h1vol) else None,
            g12=f(r.g12), p12=f(r.p12), gap=f(r.gap), cluster=bool(r.cluster), rank=int(r.rank) if pd.notna(r.rank) else None,
            limit=bool(r.limit), next_chg=f(r.next_chg),
            nb=int(m.at[r.date, 'nb']), ns=int(m.at[r.date, 'ns']), net=int(m.at[r.date, 'net']), dd=f(m.at[r.date, 'dd']), mh1=bool(m.at[r.date, 'h1'])))
    return out


def last_signals(store):
    """آخر يوم إشارة مشتري لكل سهم (للقايمة)."""
    t, m = table(store)
    if t.empty:
        return {}
    b = t[t['buy'] & t['cluster']].groupby('sym')['date'].max()
    return b.to_dict()


def market(store, n=None):
    t, m = table(store)
    return m


# ---------------- صفحة الساعة ١١ ----------------
# سهم المشتري مسيطر: كمية الساعة الأولى ≥ ضعف كمية امبارح كلها، والمشتري ≥ ٦٠٪.
# سهم البياع مسيطر: كمية الساعة الأولى ≥ مرة ونص كمية امبارح، والبياع ≥ ٦٠٪.
# ومعاهم شروط تنضيف الداتا: السهم سايل (وسيط قيمة آخر ٢٠ جلسة ≥ مليون)، والفتح مش بعيد عن إقفال امبارح بأكتر من ٢٠٪،
# والجلسة واللي قبلها مش ناقصين في الداتا.
BUY_X = 2.0
SELL_X = 1.5


def _pick(t):
    ok = (~t['thin']) & t['p11'].notna() & (t['h1val'] > 0) & (t['pvol'] > 0) & (t['med'] >= MIN_VAL) & (t['gap'] <= 0.2) & t['bsh'].notna()
    x = t['h1vol'] / t['pvol']
    b = t[ok & (x >= BUY_X) & (t['bsh'] >= BUY_SHARE)].copy()
    s = t[ok & (x >= SELL_X) & (t['bsh'] <= 1 - BUY_SHARE)].copy()
    for z in (b, s):
        z['h1x'] = z['h1vol'] / z['pvol']
        z['p11c'] = (z['last'] / z['p11'] - 1) * 100
        z['g11'] = (z['p11'] / z['prev'] - 1) * 100
    return b, s


def _rows(z, live=False):
    z = z.sort_values('h1val', ascending=False)
    f = lambda v, n=2: None if pd.isna(v) else round(float(v), n)
    return [dict(sym=r.sym, name=str(r.name) if isinstance(getattr(r, 'name', None), str) else '',
                 val=f(r.h1val / 1e6, 1), x=f(r.h1x, 1), bsh=f(r.bsh * 100, 0), p11=f(r.p11, 3), g11=f(r.g11, 1),
                 c=None if live else f(r.last, 3), p11c=None if live else f(r.p11c, 1), chg=None if live else f(r.chg, 1))
            for r in z.itertuples()]


def h11(store, noon=None):
    """كل يوم: أسهم المشتري والبياع الساعة ١١ مترتبة بفلوس الساعة الأولى، وإقفالهم، وإقفال السوق.
    noon: جدول الساعة ١١ بتاع النهارده لو الجلسة لسه ماخلصتش."""
    t, m = table(store)
    if t.empty:
        return [], {}
    if 'name' not in t:
        t['name'] = ''
    b, s = _pick(t)
    days = []
    for d in sorted(t['date'].unique(), reverse=True):
        days.append(dict(date=d, live=False, mret=round(float(m.at[d, 'ret']) * 100, 2) if d in m.index else None,
                         buy=_rows(b[b['date'] == d]), sell=_rows(s[s['date'] == d])))
    # إحصائية كل الفترة: السهم قفل فوق سعر الساعة ١١ ولا تحته
    def st(z):
        z = z[z['p11c'].notna()]
        if not len(z):
            return None
        return dict(n=int(len(z)), up=round(float((z['p11c'] > 0).mean() * 100)), dn=round(float((z['p11c'] < 0).mean() * 100)), avg=round(float(z['p11c'].mean()), 2),
                    med=round(float(z['p11c'].median()), 2), nxt=round(float(z['next_chg'].dropna().mean()), 2) if z['next_chg'].notna().any() else None)
    summ = dict(buy=st(b), sell=st(s))
    if noon is not None and len(noon) and (not days or noon['date'].iloc[0] > days[0]['date']):
        days.insert(0, _live(t, noon))
    return days, summ


def _live(t, noon):
    """يوم النهارده من ملف الساعة ١١: نقارن بآخر جلسة كاملة."""
    last = t.sort_values('date').groupby('sym').tail(1).set_index('sym')
    med = t.sort_values('date').groupby('sym')['val_m'].apply(lambda v: v.tail(20).median() if len(v) >= 10 else np.nan) * 1e6
    n = noon.set_index('sym')
    x = pd.DataFrame({'sym': n.index, 'name': n.get('name', ''), 'h1vol': n['h1vol'], 'h1b': n['h1b'], 'h1s': n['h1s'],
                      'p11': n['p11'], 'h1val': n['h1val'], 'open': n['open']}).set_index('sym')
    x['pvol'] = last['vol'].reindex(x.index)
    x['prev'] = last['last'].reindex(x.index)
    x['med'] = med.reindex(x.index)
    x['bsh'] = x['h1b'] / (x['h1b'] + x['h1s'])
    x['gap'] = (x['open'] / x['prev'] - 1).abs()
    x['thin'] = False
    x['last'] = np.nan
    x = x.reset_index()
    b, s = _pick(x)
    return dict(date=noon['date'].iloc[0], live=True, mret=None, buy=_rows(b, True), sell=_rows(s, True))


# ---------------- صفحة أول ربع ساعة ----------------
# اتختبرت على سبتمبر ٢٠٢٥ لنوفمبر ٢٠٢٦ (٢٦٠ جلسة):
# - السهم اللي نزل ٢٪ أو أكتر لحد ١٠:١٥: من سعر ١٠:١٥ لإقفال بكره وسيط +٠٫٨٪، وكسبان ٥٧٪ (أي سهم: −٠٫٣٪ و٤٣٪).
# - السهم اللي طلع ٥٪ أو أكتر لحد ١٠:١٥: من سعر ١٠:١٥ لإقفال بكره وسيط −٢٫٢٪، وكسبان ٣٦٪ بس.
DOWN15 = -2.0
UP15 = 5.0


def _pick15(t):
    ok = (~t['thin']) & t['p15'].notna() & (t['med'] >= MIN_VAL) & (t['gap'] <= 0.2) & (t['prev'] > 0)
    z = t[ok].copy()
    z['g15'] = (z['p15'] / z['prev'] - 1) * 100
    z['rest'] = (z['last'] / z['p15'] - 1) * 100
    z['hold'] = (z['nlast'] / z['p15'] - 1) * 100
    return z[z['g15'] <= DOWN15], z[z['g15'] >= UP15]


def _rows15(z, live=False):
    z = z.sort_values('val15', ascending=False)
    f = lambda v, n=2: None if pd.isna(v) else round(float(v), n)
    return [dict(sym=r.sym, name=str(r.name) if isinstance(r.name, str) else '', val=f(r.val15 / 1e6, 1),
                 p15=f(r.p15, 3), g15=f(r.g15, 1),
                 c=None if live else f(r.last, 3), rest=None if live else f(r.rest, 1),
                 nc=None if live else f(r.nlast, 3), hold=None if live else f(r.hold, 1))
            for r in z.itertuples()]


def m15(store, live=None):
    t, m = table(store)
    if t.empty:
        return [], {}
    if 'name' not in t:
        t['name'] = ''
    t = t.copy()
    t['nlast'] = t.groupby('sym')['last'].shift(-1)
    dn, up = _pick15(t)
    days = []
    for d in sorted(t['date'].unique(), reverse=True):
        days.append(dict(date=d, live=False, mret=round(float(m.at[d, 'ret']) * 100, 2) if d in m.index else None,
                         down=_rows15(dn[dn['date'] == d]), up=_rows15(up[up['date'] == d])))

    def st(z):
        z = z[z['hold'].notna()]
        if not len(z):
            return None
        return dict(n=int(len(z)), win=round(float((z['hold'] > 0).mean() * 100)), med=round(float(z['hold'].median()), 2),
                    rest=round(float(z['rest'].median()), 2))
    a = t[t['p15'].notna() & (t['med'] >= MIN_VAL) & ~t['thin']].copy()
    a['hold'] = (a['nlast'] / a['p15'] - 1) * 100
    a['rest'] = (a['last'] / a['p15'] - 1) * 100
    summ = dict(down=st(dn), up=st(up), all=st(a))
    if live is not None and len(live) and (not days or live['date'].iloc[0] > days[0]['date']):
        days.insert(0, _live15(t, live))
    return days, summ


def _live15(t, live):
    s = t.sort_values('date')
    last = s.groupby('sym').tail(1).set_index('sym')
    med = s.groupby('sym')['val_m'].apply(lambda v: v.tail(20).median() if len(v) >= 10 else np.nan) * 1e6
    n = live.set_index('sym')
    x = pd.DataFrame({'name': n.get('name', ''), 'open': n['open'], 'p15': n['p15'], 'val15': n['val15']})
    x['prev'] = last['last'].reindex(x.index)
    x['med'] = med.reindex(x.index)
    x['gap'] = (x['open'] / x['prev'] - 1).abs()
    x['thin'] = False
    x['last'] = np.nan
    x['nlast'] = np.nan
    x = x.reset_index().rename(columns={'index': 'sym'})
    dn, up = _pick15(x)
    return dict(date=live['date'].iloc[0], live=True, mret=None, down=_rows15(dn, True), up=_rows15(up, True))
