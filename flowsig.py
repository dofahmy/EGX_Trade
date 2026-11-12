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
import pandas as pd

MIN_STOCKS = 10
BUY_SHARE = 0.6
MIN_VAL = 1e6
LIMIT = 19.5

SEED = Path(__file__).resolve().parent / 'h1_seed.csv.gz'
_SEED = None
_CACHE = {}


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
        cols = ['open', 'hi', 'lo', 'last', 'prev', 'chg', 'vol', 'val_m', 'n']
        x = st[[c for c in cols if c in st]].copy()
        for c in ('h1vol', 'h1b', 'h1s', 'p12'):
            x[c] = st[c] if c in st else np.nan
        if x['h1vol'].isna().all() and len(sd):
            try:
                s = sd.loc[d]
                for c in ('h1vol', 'h1b', 'h1s', 'p12'):
                    x[c] = s[c].reindex(x.index)
            except KeyError:
                pass
        x['date'] = d
        parts.append(x.reset_index())
    if not parts:
        return pd.DataFrame(), pd.DataFrame()
    t = pd.concat(parts, ignore_index=True).sort_values(['sym', 'date']).reset_index(drop=True)
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
