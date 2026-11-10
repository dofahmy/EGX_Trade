"""
الحيطان والشموع الملونة بالحالة، والشمعة الدهبية والسودا.

- الحيطة: أكتر سعر اتلم عنده بيع في اليوم (من مستويات البيع المتخزنة)، بـ٢٪ أو أكتر من تداول اليوم.
  ولو "منطقة الحيطة" أكبر من صفر، الأسعار القريبة من بعض بتتجمع في حيطة واحدة (مهم لأمريكا).
- كل الأرقام دي بتتغير من الإعدادات (c_share, c_ratio, c_val, c_near, c_wall, c_zone).
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

# الأرقام الأصلية، وبتتغير من الإعدادات لكل سوق
CFG = dict(c_share=10.0, c_ratio=4.0, c_val=1.0, c_near=3.0, c_wall=2.0, c_zone=0.0, c_share_max=None, c_ratio_max=None)


def cfg_of(p):
    c = dict(CFG)
    for k in CFG:
        if p.get(k) is not None:
            c[k] = float(p[k])
        elif k.endswith('_max') and k in p:
            c[k] = None
    return c


def zones(lv, zone):
    """بيجمع الأسعار القريبة من بعض في حيطة واحدة: كل الأسعار اللي في حدود zone٪ من أول سعر فيها.
    سعر الحيطة = السعر اللي اتلم عنده أكتر بيع جوه المنطقة، ونسبتها = مجموع المنطقة كلها."""
    if not zone or lv.empty:
        return lv
    out = []
    for sym, g in lv.sort_values(['sym', 'price']).groupby('sym', sort=False):
        pr, sh = g['price'].to_numpy(), g['share'].to_numpy()
        i = 0
        while i < len(pr):
            j = i
            while j + 1 < len(pr) and pr[j + 1] <= pr[i] * (1 + zone / 100):
                j += 1
            k = i + int(np.argmax(sh[i:j + 1]))
            out.append((sym, float(pr[k]), float(sh[i:j + 1].sum()), float(pr[i]), float(pr[j])))
            i = j + 1
    return pd.DataFrame(out, columns=['sym', 'price', 'share', 'p_lo', 'p_hi'])


def load_all(store, zone=0.0):
    dates = store.dates()
    S, TOP, LV = {}, {}, {}
    for d in dates:
        st, lv, _ = store.load(d)
        S[d] = st
        LV[d] = lv
        TOP[d] = zones(lv, zone).sort_values('share', ascending=False).drop_duplicates('sym').set_index('sym')
    return dates, S, TOP, LV


def strength(lv, sym, wp, zone):
    """اللي اتباع عند سعر الحيطة (أو جوه منطقتها) في اليوم ده."""
    l = lv[lv['sym'] == sym]
    if zone:
        m = l[(l['price'] >= wp * (1 - zone / 100)) & (l['price'] <= wp * (1 + zone / 100))]
    else:
        m = l[np.isclose(l['price'], wp)]
    return float(m['share'].sum()) if len(m) else 0.0


def locked(s):
    """يوم متقفل على حد أدنى أو أقصى: الحيطة فيه مالهاش معنى."""
    return (s['hi'] == s['lo']) or (s['last'] == s['lo'] and s['chg'] <= -4.5) or \
        (s['last'] == s['hi'] and s['chg'] >= 4.5 and s['open'] == s['hi'])


def _status(dates, S, sym, wall_day, price, upto, near=NEAR):
    cur, below = None, 0
    for dd in dates[dates.index(wall_day):dates.index(upto) + 1]:
        if sym not in S[dd].index:
            continue
        q = (S[dd].loc[sym, 'last'] / price - 1) * 100
        if q >= -0.1:
            cur, below = 'g', 0
        else:
            below += 1
            cur = 'r' if (q <= -near or below >= 2 or cur == 'r') else 'o'
    return cur


def special(dates, S, TOP, i, sym, c_share=10.0, c_ratio=4.0, c_val=1.0, c_near=3.0, c_share_max=None, c_ratio_max=None,
            exclude=(), why=None, **_):
    """لو الشمعة دي دهبية أو سودا أو محايدة بيرجع النوع والتفاصيل، غير كده None.
    why: عداد بيتسجل فيه كل سهم وقع عند أنهي شرط."""
    def out(reason):
        if why is not None:
            why[reason] = why.get(reason, 0) + 1
        return None
    d = dates[i]
    if i < 1 or sym in exclude or sym not in S[d].index or sym not in TOP[d].index:
        return None
    a = S[d].loc[sym]
    p = S[dates[i - 1]].loc[sym] if sym in S[dates[i - 1]].index else None
    if p is None:
        return None
    if why is not None:
        why['all'] = why.get('all', 0) + 1
    if a['val_m'] < c_val or 'وثائق' in str(a['name']) or locked(a):
        return out('val')
    r = TOP[d].loc[sym]
    if r['share'] < c_share or (c_share_max is not None and r['share'] > c_share_max):
        return out('share')
    q = (a['last'] / r['price'] - 1) * 100
    if not (-0.1 <= q <= c_near):
        return out('near')
    prev = [TOP[x].loc[sym, 'share'] for x in dates[:i] if sym in TOP[x].index]
    if not prev:
        return out('ratio')
    ratio = r['share'] / float(np.median(prev))
    if ratio < c_ratio or (c_ratio_max is not None and ratio > c_ratio_max):
        return out('ratio')
    if why is not None:
        why['ok'] = why.get('ok', 0) + 1
    o_up, c_up = a['open'] > p['open'], a['last'] > p['last']
    kind = 'gold' if (o_up and c_up) else 'black' if (not o_up and not c_up) else 'neutral'
    return dict(kind=kind, wall=float(r['price']), share=round(float(r['share']), 1), ratio=round(ratio, 1))


FAST, MID = 50, 40   # أكتر نص ساعة خد كام في المية من اللي اتعمل عند الحيطة


def wall_flow(fl, pk, sym, wall, zone=0.0):
    """الكمية اللي اتعملت عند الحيطة (بالمليون) وسرعتها، وتنبيه لو فيه صفقة كبيرة في دقيقة واحدة."""
    if fl is None:
        return None
    band = max(0.2, zone / 2) / 100
    f = fl[(fl['sym'] == sym) & ((fl['price'] - wall).abs() <= wall * band)]
    tot = f['val'].sum()
    if not tot:
        return None
    b = f.groupby('k')['val'].sum()
    b = b.reindex(range(int(b.index.min()), int(b.index.max()) + 1), fill_value=0)
    best = float(b.rolling(2, min_periods=1).sum().max()) / tot * 100
    speed = 'fast' if best >= FAST else 'mid' if best >= MID else 'slow'
    out = dict(f_val=round(tot / 1e6, 1), f_best=round(best), f_speed=speed, f_block=None)
    p = pk[(pk['sym'] == sym) & ((pk['price'] - wall).abs() <= wall * band)]
    if len(p):
        top = p.sort_values('pk_val', ascending=False).iloc[0]
        if top['pk_val'] >= 0.3 * tot and top['pk_n'] <= 10:
            mi = int(top['mi'])
            out['f_block'] = dict(val=round(top['pk_val'] / 1e6, 1), n=int(top['pk_n']),
                                  t='%d:%02d' % (mi // 60, mi % 60), pct=round(top['pk_val'] / tot * 100))
    return out


def scan(store, exclude=(), why=None, **cfg):
    """كل الشموع الدهبية والسودا والمحايدة في كل الجلسات، ومعاها النتيجة بعدها."""
    cfg = cfg_of(cfg)
    dates, S, TOP, LV = load_all(store, cfg['c_zone'])
    rows, FL = [], {}
    last = dates[-1] if dates else None
    for i, d in enumerate(dates):
        for sym in TOP[d].index:
            sp = special(dates, S, TOP, i, sym, exclude=set(exclude), why=why, **cfg)
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
            if d not in FL:
                FL[d] = store.load_flow(d) if hasattr(store, 'load_flow') else (None, None)
            wf = wall_flow(*FL[d], sym, sp['wall'], cfg['c_zone']) or {}
            rows.append(dict(sp, **wf, date=d, sym=sym, name=a['name'], close=float(a['last']), chg=float(a['chg']),
                             val_m=float(a['val_m']), r1=res[0], r2=res[1], r3=res[2], now=now))
    return rows


def series(store, sym, **cfg):
    """شموع سهم واحد بالألوان والعرض، لصفحة الرسم."""
    cfg = cfg_of(cfg)
    zone, near = cfg['c_zone'], cfg['c_near']
    dates, S, TOP, LV = load_all(store, zone)
    days = [d for d in dates if sym in S[d].index]
    walls = []
    for d in days:
        if sym in TOP[d].index and TOP[d].loc[sym, 'share'] >= cfg['c_wall'] and not locked(S[d].loc[sym]):
            walls.append((d, float(TOP[d].loc[sym, 'price'])))
    out = []
    for d in days:
        s = S[d].loc[sym]
        c = float(s['last'])
        act = [(w, _status(dates, S, sym, w[0], w[1], d, near)) for w in walls if w[0] <= d]
        close = [(w, st) for w, st in act if abs(c / w[1] - 1) * 100 <= near]
        wp = None
        if close:
            today = [x for x in close if x[0][0] == d]
            (w, stt) = today[0] if today else min(close, key=lambda x: abs(c / x[0][1] - 1))
            wp = w[1]
            col = {'g': 'green', 'o': 'orange', 'r': 'red'}[stt]
        else:
            col = 'lightgreen' if any(stt == 'g' and w[1] < c for w, stt in act) else 'gray'
        st_ = strength(LV[d], sym, wp, zone) if wp is not None else 0.0
        sp = special(dates, S, TOP, dates.index(d), sym, **cfg)
        out.append(dict(d=d, o=float(s['open']), h=float(s['hi']), l=float(s['lo']), c=c,
                        chg=float(s['chg']) if pd.notna(s['chg']) else 0.0, col=col, wall=wp,
                        str=round(st_, 1), kind=sp['kind'] if sp else None))
    return out
