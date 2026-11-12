# -*- coding: utf-8 -*-
"""المجموعات: أسهم بتتحرك مع بعض (نفس أسابيع انفجار التداول، ونفس شرا وبيع الصفقات الكبيرة).
المجموعات نفسها محسوبة من ١٣ شهر داتا ومتخزنة في groups.json.
لكل سهم في كل أسبوع: التداول كام ضعف المعتاد، وصافي الصفقات الكبيرة كنسبة من التداول، وتغير السعر."""
import json
from pathlib import Path
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
_SEED = None
_CACHE = {}


def groups():
    f = HERE / 'groups.json'
    return json.loads(f.read_text(encoding='utf-8')) if f.exists() else []


def seed():
    global _SEED
    if _SEED is None:
        f = HERE / 'big_seed.csv.gz'
        _SEED = pd.read_csv(f, dtype={'sym': str}) if f.exists() else pd.DataFrame()
    return _SEED


def daily(store):
    """لكل سهم ولكل جلسة: قيمة التداول، وشرا وبيع الصفقات الكبيرة، والإغلاق."""
    dates = store.dates()
    try:
        stamp = tuple((d, (store.sdir(d) / 'stats.csv').stat().st_mtime) for d in dates)
    except Exception:
        stamp = None
    key = str(getattr(store, 'root', id(store)))
    if stamp is not None and key in _CACHE and _CACHE[key][0] == stamp:
        return _CACHE[key][1]
    sd = seed()
    have = set(zip(sd['date'], sd['sym'])) if len(sd) else set()
    parts = []
    for d in dates:
        try:
            st = pd.read_csv(store.sdir(d) / 'stats.csv', dtype={'sym': str})
        except Exception:
            continue
        x = pd.DataFrame({'date': d, 'sym': st['sym'], 'last': st['last'], 'name': st['name'],
                          'val': st['val_m'] * 1e6})
        for c in ('bigb', 'bigs', 'bigv'):
            x[c] = st[c] if c in st else np.nan
        parts.append(x)
    t = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if len(t) and len(sd):
        # الجلسات القديمة اللي اترفعت قبل ما التطبيق يحسب الصفقات الكبيرة: ناخدها من big_seed
        s = sd.set_index(['date', 'sym'])
        miss = t['bigb'].isna()
        idx = pd.MultiIndex.from_frame(t.loc[miss, ['date', 'sym']])
        for c in ('bigb', 'bigs', 'bigv'):
            t.loc[miss, c] = s[c].reindex(idx).to_numpy()
    if stamp is not None:
        _CACHE[key] = (stamp, t)
    return t


def weekly(store, weeks=12, end=None):
    t = daily(store)
    if t.empty:
        return [], [], []
    t = t.copy()
    t['wk'] = pd.to_datetime(t['date']).dt.to_period('W-THU')
    t['bign'] = t['bigb'] - t['bigs']
    w = t.groupby(['sym', 'wk']).agg(val=('val', 'sum'), bign=('bign', 'sum'), bigok=('bigb', lambda v: v.notna().all()),
                                     c=('last', 'last'), days=('date', 'nunique')).reset_index()
    w = w[w['days'] >= 2].sort_values(['sym', 'wk'])  # أسبوع فيه جلسة واحدة مالوش معنى
    w['vday'] = w['val'] / w['days']
    # السوق كله: تداول الأسبوع كام ضعف آخر ١٢ أسبوع
    m = w.groupby('wk')['val'].sum() / w.groupby('wk')['days'].max()
    mm = m / m.shift(1).rolling(12, min_periods=4).median()
    # السهم: تداول الأسبوع كام ضعف آخر ١٢ أسبوع ليه هو، مقسوم على السوق
    base = w.groupby('sym')['vday'].transform(lambda v: v.shift(1).rolling(12, min_periods=4).median())
    w['mult'] = w['vday'] / base / w['wk'].map(mm)
    w['bn'] = np.where(w['bigok'], w['bign'] / w['val'], np.nan)
    w['ret'] = w.groupby('sym')['c'].pct_change()
    allw = sorted(w['wk'].unique())
    if end:
        allw = [k for k in allw if str(k.end_time.date()) <= end] or allw
    wks = allw[-weeks:] if weeks else allw
    names = t.drop_duplicates('sym', keep='last').set_index('sym')['name'].to_dict()
    out = []
    for g in groups():
        rows = []
        for s in g['members']:
            x = w[(w['sym'] == s)].set_index('wk')
            cells = []
            for k in wks:
                if k in x.index:
                    r = x.loc[k]
                    cells.append(dict(m=None if pd.isna(r['mult']) else round(float(r['mult']), 1),
                                      bn=None if pd.isna(r['bn']) else round(float(r['bn']) * 100),
                                      r=None if pd.isna(r['ret']) else round(float(r['ret']) * 100, 1)))
                else:
                    cells.append(None)
            rows.append(dict(sym=s, name=str(names.get(s, '')), cells=cells))
        last = [((r['cells'][-1] or {}).get('m') or 0) for r in rows]
        hot = [rows[i]['sym'] for i in np.argsort(last)[::-1] if last[i] >= 3][:3]
        out.append(dict(members=g['members'], sim=g.get('sim'), title=g.get('title'), rows=rows, hot=hot))
    labels = [str(k.end_time.date())[2:] for k in wks]
    ends = [str(k.end_time.date()) for k in sorted(w['wk'].unique())]
    return out, labels, ends
