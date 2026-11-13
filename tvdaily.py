# -*- coding: utf-8 -*-
"""الشموع اليومية من تريدنج فيو (فتح وأعلى وأوطى وإقفال رسمي، ومتعدلة بالإجراءات زي الشارت).

- بتتنزل في الخلفية: أول مرة ٥٠٠ جلسة لكل سهم، وبعد كده آخر ٢٠ جلسة بس وبتتضاف.
- بتتحدث لوحدها كل يوم من الأحد للخميس الساعة ٣:٤٥ العصر و٩ بالليل (بتوقيت القاهرة)، ولما السيرفر يقوم.
- لو تريدنج فيو رجّع أسعار مختلفة للأيام القديمة (إجراء جديد على السهم)، السهم بيتنزل من الأول.
- الملف: DATA_DIR/tv_daily.csv  (date, sym, open, high, low, close, volume)
"""
import datetime as dt
import os
import threading
import time
from pathlib import Path

import pandas as pd

COLS = ['date', 'sym', 'open', 'high', 'low', 'close', 'volume']
FULL = 500
RECENT = 20
status = dict(running=False, last_run=None, last_date=None, syms=0, failed=0, error=None)
_LOCK = threading.Lock()
_CACHE = {}


def path(root):
    return Path(root) / 'tv_daily.csv'


def load(root):
    p = path(root)
    if not p.exists():
        return pd.DataFrame(columns=COLS)
    m = p.stat().st_mtime
    k = str(p)
    if k in _CACHE and _CACHE[k][0] == m:
        return _CACHE[k][1]
    d = pd.read_csv(p, dtype={'sym': str})
    _CACHE[k] = (m, d)
    return d


def _client():
    from tvDatafeed import TvDatafeed
    u, p = os.environ.get('TV_USERNAME'), os.environ.get('TV_PASSWORD')
    try:
        return TvDatafeed(u, p) if u and p else TvDatafeed()
    except Exception:
        return TvDatafeed()


def _get(tv, sym, bars):
    from tvDatafeed import Interval
    for _ in range(2):
        try:
            d = tv.get_hist(symbol=sym, exchange='EGX', interval=Interval.in_daily, n_bars=bars)
            if d is not None and len(d):
                d = d.reset_index().rename(columns={'datetime': 'date'})
                d['date'] = pd.to_datetime(d['date']).dt.normalize()
                d['sym'] = sym
                return d[['date', 'sym', 'open', 'high', 'low', 'close', 'volume']]
        except Exception:
            pass
        time.sleep(2)
    return None


def _align(d, sessions):
    """تريدنج فيو ساعات بيكتب تاريخ الشمعة متزحلق يوم (فرق توقيت). بنختار الزحلقة اللي بتطابق جلسات سيجما أكتر."""
    if d.empty or not sessions:
        return d
    ss = set(sessions)
    best, shift = -1, 0
    for k in (0, 1, -1):
        x = (d['date'] + pd.Timedelta(days=k)).dt.strftime('%Y-%m-%d')
        hit = x.isin(ss).mean()
        if hit > best:
            best, shift = hit, k
    d = d.copy()
    d['date'] = (d['date'] + pd.Timedelta(days=shift)).dt.strftime('%Y-%m-%d')
    return d


def refresh(store, log=print):
    if not _LOCK.acquire(blocking=False):
        return
    status.update(running=True, error=None)
    try:
        dates = store.dates()
        if not dates:
            return
        st = pd.read_csv(store.sdir(dates[-1]) / 'stats.csv', dtype={'sym': str})
        syms = sorted(st['sym'].dropna().unique())
        old = load(store.root)
        have = set(old['sym']) if len(old) else set()
        tv = _client()
        parts, failed = [], 0
        for k, s in enumerate(syms, 1):
            full = s not in have
            d = _get(tv, s, FULL if full else RECENT)
            if d is None:
                failed += 1
                continue
            d = _align(d, dates)
            if not full:
                # لو الأسعار القديمة اتغيرت (إجراء جديد اتعدل عليه)، ننزل السهم كله من جديد
                o = old[(old['sym'] == s)].set_index('date')['close']
                n = d.set_index('date')['close']
                common = n.index.intersection(o.index)
                if len(common) and ((n[common] / o[common] - 1).abs() > 0.005).any():
                    d2 = _get(tv, s, FULL)
                    if d2 is not None:
                        d = _align(d2, dates)
                        old = old[old['sym'] != s]
            parts.append(d)
            if k % 25 == 0:
                log(f'[تريدنج فيو] {k}/{len(syms)}')
            time.sleep(0.3)
        if parts:
            new = pd.concat(parts, ignore_index=True)
            allp = pd.concat([old, new], ignore_index=True).drop_duplicates(['sym', 'date'], keep='last')
            allp = allp.sort_values(['sym', 'date'])[COLS]
            tmp = path(store.root).with_suffix('.tmp')
            allp.to_csv(tmp, index=False)
            os.replace(tmp, path(store.root))
            status.update(last_date=allp['date'].max(), syms=int(allp['sym'].nunique()))
        status.update(failed=failed, last_run=dt.datetime.now().strftime('%Y-%m-%d %H:%M'))
        log(f'[تريدنج فيو] خلص: {status}')
    except Exception as e:
        status['error'] = str(e)[:300]
        log(f'[تريدنج فيو] فشل: {e}')
    finally:
        status['running'] = False
        _LOCK.release()


def start(store, log=print):
    """تحديث أول ما السيرفر يقوم، وبعدين كل يوم في المواعيد."""
    def loop():
        time.sleep(20)
        refresh(store, log)
        done = set()
        while True:
            try:
                from zoneinfo import ZoneInfo
                now = dt.datetime.now(ZoneInfo('Africa/Cairo'))
            except Exception:
                now = dt.datetime.utcnow() + dt.timedelta(hours=2)
            key = (now.date(), now.hour)
            if now.weekday() in (6, 0, 1, 2, 3) and (now.hour, now.minute) in ((15, 45), (21, 0)) and key not in done:
                done.add(key)
                refresh(store, log)
            time.sleep(30)
    threading.Thread(target=loop, daemon=True).start()
