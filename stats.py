# -*- coding: utf-8 -*-
"""عداد زيارات بسيط جوه الموقع.
- مابيحفظش عنوان الزائر: بيحفظ رقم مشفّر من (العنوان + المتصفح + مفتاح سري بيتغير كل يوم)،
  فبيعرف الزائر المختلف في نفس اليوم بس، ومايقدرش يرجع للعنوان ولا يتتبع حد من يوم لليوم.
- بيعد الصفحات اللي بتفتح بنجاح، والشارتات اللي اتفتحت لكل سهم.
"""
import datetime as dt
import hashlib
import os
import secrets
import sqlite3
import threading
from pathlib import Path

_LOCK = threading.Lock()
_DB = None
_SALT = {}
BOTS = ('bot', 'crawl', 'spider', 'slurp', 'preview', 'monitor', 'curl', 'wget', 'python', 'headless')
SKIP = ('/stats', '/tv', '/candles/data', '/static', '/favicon')


def _today():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo('Africa/Cairo')).date().isoformat()
    except Exception:
        return (dt.datetime.utcnow() + dt.timedelta(hours=2)).date().isoformat()


def init(root):
    global _DB
    Path(root).mkdir(parents=True, exist_ok=True)
    _DB = str(Path(root) / 'stats.db')
    with sqlite3.connect(_DB) as c:
        c.execute('CREATE TABLE IF NOT EXISTS views (day TEXT, path TEXT, n INTEGER, PRIMARY KEY(day, path))')
        c.execute('CREATE TABLE IF NOT EXISTS visitors (day TEXT, vid TEXT, PRIMARY KEY(day, vid))')
        c.execute('CREATE TABLE IF NOT EXISTS charts (day TEXT, sym TEXT, n INTEGER, PRIMARY KEY(day, sym))')


def _vid(day, ip, ua):
    if day not in _SALT:
        _SALT.clear()
        _SALT[day] = secrets.token_hex(16)  # مفتاح جديد كل يوم، ومابيتحفظش
    return hashlib.sha256((_SALT[day] + '|' + ip + '|' + ua).encode()).hexdigest()[:16]


def hit(request, response):
    """بيتنادى بعد كل طلب. أي خطأ هنا مايأثرش على الموقع."""
    try:
        if _DB is None or request.method != 'GET' or response.status_code != 200:
            return
        if not (response.mimetype or '').startswith('text/html'):
            return
        p = request.path
        if p.startswith(SKIP):
            return
        ua = (request.headers.get('User-Agent') or '')[:300]
        if not ua or any(b in ua.lower() for b in BOTS):
            return
        ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '').split(',')[0].strip()
        day = _today()
        sym = (request.args.get('sym') or '').upper()[:12] if p == '/candles' else ''
        with _LOCK, sqlite3.connect(_DB) as c:
            c.execute('INSERT INTO views VALUES (?,?,1) ON CONFLICT(day, path) DO UPDATE SET n = n + 1', (day, p))
            c.execute('INSERT OR IGNORE INTO visitors VALUES (?,?)', (day, _vid(day, ip, ua)))
            if sym:
                c.execute('INSERT INTO charts VALUES (?,?,1) ON CONFLICT(day, sym) DO UPDATE SET n = n + 1', (day, sym))
    except Exception:
        pass


def report(days=60):
    if _DB is None:
        return dict(days=[], syms=[], pages=[], tot={})
    since = (dt.date.fromisoformat(_today()) - dt.timedelta(days=days - 1)).isoformat()
    with sqlite3.connect(_DB) as c:
        v = dict(c.execute('SELECT day, SUM(n) FROM views WHERE day >= ? GROUP BY day', (since,)).fetchall())
        u = dict(c.execute('SELECT day, COUNT(*) FROM visitors WHERE day >= ? GROUP BY day', (since,)).fetchall())
        ch = dict(c.execute('SELECT day, SUM(n) FROM charts WHERE day >= ? GROUP BY day', (since,)).fetchall())
        syms = c.execute('SELECT sym, SUM(n) s FROM charts WHERE day >= ? GROUP BY sym ORDER BY s DESC LIMIT 30', (since,)).fetchall()
        pages = c.execute('SELECT path, SUM(n) s FROM views WHERE day >= ? GROUP BY path ORDER BY s DESC LIMIT 15', (since,)).fetchall()
    ds = sorted(set(v) | set(u), reverse=True)
    rows = [dict(d=d, v=v.get(d, 0), u=u.get(d, 0), c=ch.get(d, 0)) for d in ds]
    tot = dict(v=sum(r['v'] for r in rows), u=sum(r['u'] for r in rows), c=sum(r['c'] for r in rows))
    return dict(days=rows, syms=syms, pages=pages, tot=tot)


def password():
    return os.environ.get('STATS_PASSWORD', '')
