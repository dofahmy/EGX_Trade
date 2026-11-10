"""
الحيطان والشموع الملونة بالحالة، والشمعة الدهبية والسودا.

- الحيطة: أكتر سعر اتلم عنده بيع في اليوم (من مستويات البيع المتخزنة)، بـ٢٪ أو أكتر من تداول اليوم.
- حالة الحيطة يوم بيوم:
    أخضر  = كل الإقفالات بعدها عليها أو فوقها
    برتقالي = قفل تحتها بأقل من ٣٪ أول يوم
    أحمر  = قفل تحتها بـ٣٪ أو أكتر، أو يومين ورا بعض
- لون الشمعة = حالة أقرب حيطة في حدود ٣٪ من الإقفال. لو مفيش حيطة قريبة:
    أخضر فاتح = فوق حيطة اتحمت (ماشي فوق دعم)، رمادي = مفيش دعم تحته.
- عرض الشمعة = اللي اتباع عند سعر الحيطة في اليوم ده، كنسبة من تداول اليوم.
- الشمعة الدهبية: حيطة اتلم عندها ١٠٪ أو أكتر، وأتخن ٤ مرات أو أكتر من عادة السهم،
  والإقفال عليها أو فوقها بـ٣٪ أو أقل، والفتح أعلى من فتح الشمعة اللي قبلها والإقفال أعلى من إقفالها.
  لو الاتنين أقل = سودا (خطر بيع). لو واحد بس = محايدة.
"""
import numpy as np
import pandas as pd

MIN_WALL = 2.0
NEAR = 3.0


def load_all(store):
    dates = store.dates()
    S, TOP, LV = {}, {}, {}
    for d in dates:
        st, lv, _ = store.load(d)
        S[d] = st
        LV[d] = lv
        TOP[d] = lv.sort_values('share', ascending=False).drop_duplicates('sym').set_index('sym')
    return dates, S, TOP, LV


def locked(s):
    """يوم متقفل على حد أدنى أو أقصى: الحيطة فيه مالهاش معنى."""
    return (s['hi'] == s['lo']) or (s['last'] == s['lo'] and s['chg'] <= -4.5) or \
        (s['last'] == s['hi'] and s['chg'] >= 4.5 and s['open'] == s['hi'])


def _status(dates, S, sym, wall_day, price, upto):
    cur, below = None, 0
    for dd in dates[dates.index(wall_day):dates.index(upto) + 1]:
        if sym not in S[dd].index:
            continue
        q = (S[dd].loc[sym, 'last'] / price - 1) * 100
        if q >= -0.1:
            cur, below = 'g', 0
        else:
            below += 1
            cur = 'r' if (q <= -NEAR or below >= 2 or cur == 'r') else 'o'
    return cur


def special(dates, S, TOP, i, sym, g_share=10.0, g_ratio=4.0, min_val=1.0, exclude=()):
    """لو الشمعة دي دهبية أو سودا أو محايدة بيرجع النوع والتفاصيل، غير كده None."""
    d = dates[i]
    if i < 1 or sym in exclude or sym not in S[d].index or sym not in TOP[d].index:
        return None
    a = S[d].loc[sym]
    p = S[dates[i - 1]].loc[sym] if sym in S[dates[i - 1]].index else None
    if p is None or a['val_m'] < min_val or 'وثائق' in str(a['name']) or locked(a):
        return None
    r = TOP[d].loc[sym]
    if r['share'] < g_share:
        return None
    q = (a['last'] / r['price'] - 1) * 100
    if not (-0.1 <= q <= NEAR):
        return None
    prev = [TOP[x].loc[sym, 'share'] for x in dates[:i] if sym in TOP[x].index]
    if not prev:
        return None
    ratio = r['share'] / float(np.median(prev))
    if ratio < g_ratio:
        return None
    o_up, c_up = a['open'] > p['open'], a['last'] > p['last']
    kind = 'gold' if (o_up and c_up) else 'black' if (not o_up and not c_up) else 'neutral'
    return dict(kind=kind, wall=float(r['price']), share=round(float(r['share']), 1), ratio=round(ratio, 1))


def scan(store, exclude=(), **cfg):
    """كل الشموع الدهبية والسودا والمحايدة في كل الجلسات، ومعاها النتيجة بعدها."""
    dates, S, TOP, LV = load_all(store)
    rows = []
    last = dates[-1] if dates else None
    for i, d in enumerate(dates):
        for sym in TOP[d].index:
            sp = special(dates, S, TOP, i, sym, exclude=set(exclude), **cfg)
            if not sp:
                continue
            a = S[d].loc[sym]
            res = []
            for j in (1, 2, 3):
                if i + j < len(dates) and sym in S[dates[i + j]].index:
                    res.append(round((S[dates[i + j]].loc[sym, 'last'] / a['last'] - 1) * 100, 2))
                else:
                    res.append(None)
            now = round((S[last].loc[sym, 'last'] / a['last'] - 1) * 100, 2) if d != last and sym in S[last].index else None
            rows.append(dict(sp, date=d, sym=sym, name=a['name'], close=float(a['last']), chg=float(a['chg']),
                             val_m=float(a['val_m']), r1=res[0], r2=res[1], r3=res[2], now=now))
    return rows


def series(store, sym):
    """شموع سهم واحد بالألوان والعرض، لصفحة الرسم."""
    dates, S, TOP, LV = load_all(store)
    days = [d for d in dates if sym in S[d].index]
    walls = []
    for d in days:
        if sym in TOP[d].index and TOP[d].loc[sym, 'share'] >= MIN_WALL and not locked(S[d].loc[sym]):
            walls.append((d, float(TOP[d].loc[sym, 'price'])))
    out = []
    for d in days:
        s = S[d].loc[sym]
        c = float(s['last'])
        act = [(w, _status(dates, S, sym, w[0], w[1], d)) for w in walls if w[0] <= d]
        near = [(w, st) for w, st in act if abs(c / w[1] - 1) * 100 <= NEAR]
        wp = None
        if near:
            today = [x for x in near if x[0][0] == d]
            (w, stt) = today[0] if today else min(near, key=lambda x: abs(c / x[0][1] - 1))
            wp = w[1]
            col = {'g': 'green', 'o': 'orange', 'r': 'red'}[stt]
        else:
            col = 'lightgreen' if any(stt == 'g' and w[1] < c for w, stt in act) else 'gray'
        strength = 0.0
        if wp is not None:
            l = LV[d]
            m = l[(l['sym'] == sym) & (np.isclose(l['price'], wp))]
            strength = float(m['share'].sum()) if len(m) else 0.0
        sp = special(dates, S, TOP, dates.index(d), sym)
        out.append(dict(d=d, o=float(s['open']), h=float(s['hi']), l=float(s['lo']), c=c,
                        chg=float(s['chg']) if pd.notna(s['chg']) else 0.0, col=col, wall=wp,
                        str=round(strength, 1), kind=sp['kind'] if sp else None))
    return out
