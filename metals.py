# -*- coding: utf-8 -*-
"""إشارات الشراء على الدهب والفضة، فريم الساعة، بنفس قاعدة القاع المزدوج على CCI بتاعة الأسهم.
الداتا: شموع ساعة من تريدنج فيو (OANDA:XAUUSD و OANDA:XAGUSD)، آخر ٥٠٠٠ شمعة، وبتتحدث كل ساعة في الخلفية.
الوقت بتوقيت القاهرة.
الملوّن (طلعة بفوليوم كبير جدا): شمعة من ساعة الإشارة لحد ١٠ شموع بعدها، طلعت ١٫٥ ضعف متوسط المدى (ATR ١٤)
أو أكتر، وفوليومها ٣ أضعاف وسيط آخر ١٢٠ شمعة أو أكتر.
"""
import datetime as dt
import os
import threading
import time

import numpy as np
import pandas as pd

import xbadger

SYMS = {'XAUUSD': 'الدهب', 'XAGUSD': 'الفضة'}
EXCH = 'OANDA'
BARS = 5000
WAIT = 10
ATR_X = 1.5
VOL_X = 3.0
AHEAD = 60
status = {'running': False, 'last': None, 'error': None, 'rows': {}}
_LOCK = threading.Lock()
_CACHE = {}


def _path(root, sym):
    return os.path.join(root, f'metal_{sym}_1h.csv')


def _to_cairo(ts):
    ts = pd.to_datetime(ts)
    try:
        if ts.dt.tz is None:
            ts = ts.dt.tz_localize('UTC')
        return ts.dt.tz_convert('Africa/Cairo').dt.tz_localize(None)
    except Exception:
        return ts + pd.Timedelta(hours=2)


def refresh(root, log=print):
    if not _LOCK.acquire(blocking=False):
        return
    status['running'] = True
    try:
        from tvDatafeed import TvDatafeed, Interval
        u, p = os.environ.get('TV_USERNAME'), os.environ.get('TV_PASSWORD')
        try:
            tv = TvDatafeed(u, p) if u and p else TvDatafeed()
        except Exception:
            tv = TvDatafeed()
        for sym in SYMS:
            d = None
            for _ in range(3):
                try:
                    d = tv.get_hist(symbol=sym, exchange=EXCH, interval=Interval.in_1_hour, n_bars=BARS)
                    if d is not None and len(d):
                        break
                except Exception as e:
                    status['error'] = str(e)[:200]
                time.sleep(3)
            if d is None or not len(d):
                log(f'[الدهب والفضة] مفيش داتا لـ{sym}')
                continue
            d = d.reset_index().rename(columns={'datetime': 'time'})
            d['time'] = _to_cairo(d['time'])
            d = d[['time', 'open', 'high', 'low', 'close', 'volume']]
            p = _path(root, sym)
            if os.path.exists(p):   # نحتفظ بالقديم اللي خرج من آخر ٥٠٠٠ شمعة
                old = pd.read_csv(p, parse_dates=['time'])
                d = pd.concat([old[old['time'] < d['time'].min()], d], ignore_index=True)
            d.sort_values('time').to_csv(p, index=False)
            status['rows'][sym] = len(d)
        status['last'] = dt.datetime.utcnow().isoformat(timespec='minutes')
        _CACHE.clear()
    except Exception as e:
        status['error'] = str(e)[:200]
        log(f'[الدهب والفضة] فشل: {e}')
    finally:
        status['running'] = False
        _LOCK.release()


def start(root, log=print):
    def loop():
        time.sleep(40)
        while True:
            refresh(root, log)
            time.sleep(3600 - time.time() % 3600 + 120)   # بعد كل ساعة بدقيقتين
    threading.Thread(target=loop, daemon=True).start()


def load(root, sym):
    p = _path(root, sym)
    if not os.path.exists(p):
        return pd.DataFrame(columns=['time', 'open', 'high', 'low', 'close', 'volume'])
    return pd.read_csv(p, parse_dates=['time']).sort_values('time').reset_index(drop=True)


def _atr(h, l, cc, n=14):
    pc = np.r_[np.nan, cc[:-1]]
    tr = np.fmax(h, pc) - np.fmin(l, pc)
    return pd.Series(tr).rolling(n).mean().to_numpy()


def signals(root, sym):
    d = load(root, sym)
    key = (sym, len(d), str(d['time'].iloc[-1]) if len(d) else '')
    if key in _CACHE:
        return _CACHE[key]
    out = []
    if len(d) > 60:
        hi, lo, cc = (d[k].astype(float).to_numpy() for k in ('high', 'low', 'close'))
        vol = d['volume'].astype(float).to_numpy()
        times = d['time'].dt.strftime('%Y-%m-%d %H:%M').to_numpy()
        c, m, _, _ = xbadger.cci(hi, lo, cc)
        atr = _atr(hi, lo, cc)
        med = pd.Series(vol).shift(1).rolling(120, min_periods=40).median().to_numpy()
        n = len(cc)
        for i, k1, k2 in xbadger.wsig(c, m, cc):
            b = None
            for j in range(max(i, 1), min(n, i + 1 + WAIT)):
                if atr[j - 1] > 0 and med[j] > 0 and cc[j] - cc[j - 1] >= ATR_X * atr[j - 1] and vol[j] >= VOL_X * med[j]:
                    b = j
                    break
            f = cc[i + 1:i + 1 + AHEAD]
            mx = mxn = None
            if len(f):
                k = int(np.nanargmax(f))
                mx, mxn = (f[k] / cc[i] - 1) * 100, k + 1
            out.append(dict(time=times[i], close=cc[i], t1=times[k1], t2=times[k2],
                            big=times[b] if b is not None else None,
                            bigchg=(cc[b] / cc[b - 1] - 1) * 100 if b is not None else None,
                            mx=mx, mxn=mxn, bars=len(f), now=(cc[-1] / cc[i] - 1) * 100))
    r = list(reversed(out))
    _CACHE[key] = r
    return [dict(x) for x in r]


def chart(root, sym, mark=None, span=120):
    """شموع + CCI حوالين الإشارة (أو آخر الشموع)."""
    d = load(root, sym)
    if not len(d):
        return []
    hi, lo, cc = (d[k].astype(float).to_numpy() for k in ('high', 'low', 'close'))
    c, m, _, _ = xbadger.cci(hi, lo, cc)
    times = d['time'].dt.strftime('%Y-%m-%d %H:%M').to_numpy()
    k = len(d) - 1
    if mark:
        w = np.where(times >= mark)[0]
        k = int(w[0]) if len(w) else k
        a, b = max(0, k - span), min(len(d), k + span + 1)
    else:
        a, b = max(0, len(d) - span * 2), len(d)
    r = lambda v: None if v is None or not np.isfinite(v) else round(float(v), 4)
    return [dict(t=times[i], o=r(d['open'][i]), h=r(hi[i]), l=r(lo[i]), c=r(cc[i]), cci=r(c[i]), m=r(m[i])) for i in range(a, b)]
