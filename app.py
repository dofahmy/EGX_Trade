import os, json, uuid, threading, datetime as dt
from pathlib import Path
from functools import wraps

from flask import Flask, request, redirect, url_for, render_template, Response, abort

from engine import Store, process_upload, analyze, METHODS

DATA_DIR = os.environ.get('DATA_DIR', '/data' if Path('/data').exists() else './data')
PASSWORD = os.environ.get('APP_PASSWORD', '')

BASE = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE / 'templates'))
# يدوّر على ملفات الصفحات جوه فولدر templates، ولو مش موجود يدوّر جنب app.py
from jinja2 import ChoiceLoader, FileSystemLoader
app.jinja_loader = ChoiceLoader([FileSystemLoader(str(BASE / 'templates')), FileSystemLoader(str(BASE))])
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024
store = Store(DATA_DIR)


def auth(f):
    @wraps(f)
    def w(*a, **k):
        if PASSWORD:
            a_ = request.authorization
            if not a_ or a_.password != PASSWORD:
                return Response('محتاج كلمة السر', 401, {'WWW-Authenticate': 'Basic realm="signals"'})
        return f(*a, **k)
    return w


DEFAULTS = dict(day_win=20, min_prices=7, threshold=10, mult=2.0, min_win=20, max_win=None,
                min_share=10.0, gap=3.0, gap_max=None, hold=2, warn_share=2.0)
TYPES = dict(day_win=int, min_prices=int, threshold=int, mult=float, min_win=int, max_win=int,
             min_share=float, gap=float, gap_max=float, hold=int, warn_share=float)
OPTIONAL = {'max_win', 'gap_max'}
SETTINGS_FILE = Path(DATA_DIR) / 'settings.json'


def load_settings():
    s = dict(DEFAULTS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding='utf-8')))
    except Exception:
        pass
    return s


def parse(src, base):
    out = dict(base)
    for k, t in TYPES.items():
        if k not in src:
            continue
        v = (src.get(k) or '').strip()
        if v == '':
            if k in OPTIONAL:
                out[k] = None
            continue
        try:
            out[k] = t(float(v)) if t is int else t(v)
        except Exception:
            pass
    return out


def params():
    """الإعدادات المحفوظة، ولو فيه قيم في الرابط بتتطبق فوقها للتجربة من غير حفظ."""
    return parse(request.args, load_settings())


def cairo_today():
    return (dt.datetime.utcnow() + dt.timedelta(hours=2)).date().isoformat()


@app.route('/')
@auth
def index():
    p = params()
    days, trades, state, warns = analyze(store, **p)
    latest = days[-1] if days else None
    # كل إشارة مرة واحدة، ومعاها الطرق اللي طلّعتها
    merged = {}
    for m, tl in trades.items():
        for t in tl:
            x = merged.setdefault(t['signal_date'], dict(t, methods=[]))
            x['methods'].append(m)
    sigs = sorted(merged.values(), key=lambda t: t['signal_date'], reverse=True)
    open_sigs = [t for t in sigs if t['buy_date'] and not t['sell_date']]
    closed = [t for t in sigs if t['avg'] is not None]
    def summary(lst):
        if not lst:
            return None
        n = len(lst)
        s = dict(n=n, avg=round(sum(t['avg'] for t in lst) / n, 2),
                 market=round(sum(t['market'] for t in lst) / n, 2),
                 trades=sum(t['n'] for t in lst), wins=sum(t['wins'] for t in lst))
        okn = sum(t.get('ok_n') or 0 for t in lst)
        s['ok_n'] = okn
        if okn:
            s['ok_avg'] = round(sum((t['ok_avg'] or 0) * t['ok_n'] for t in lst if t.get('ok_n')) / okn, 2)
            s['ok_wins'] = sum(t['ok_wins'] for t in lst if t.get('ok_n'))
        return s
    totals = {m: summary([t for t in closed if m in t['methods']]) for m in METHODS}
    tab = request.args.get('tab', 'today')
    return render_template('index.html', p=p, days=days, state=state, latest=latest,
                           today=cairo_today(), methods=METHODS, warns=warns,
                           open_sigs=open_sigs, closed=closed, totals=totals, tab=tab)


@app.route('/day/<date>')
@auth
def day(date):
    p = params()
    days, _, _, _ = analyze(store, **p)
    d = next((x for x in days if x['date'] == date), None)
    if not d:
        abort(404)
    return render_template('day.html', p=p, d=d)


@app.route('/upload', methods=['POST'])
@auth
def upload():
    f = request.files.get('file')
    date = request.form.get('date') or cairo_today()
    try:
        dt.date.fromisoformat(date)
    except Exception:
        return 'التاريخ مش مظبوط', 400
    if not f or not f.filename:
        return 'اختاري ملف', 400
    ext = Path(f.filename).suffix.lower() or '.xlsx'
    if ext not in ('.xlsx', '.xlsm', '.rar', '.zip'):
        return 'الملف لازم يكون إكسل أو مضغوط', 400
    jid = uuid.uuid4().hex[:10]
    tmp = Path(DATA_DIR) / 'jobs' / f'{jid}{ext}'
    f.save(tmp)
    store.job_set(jid, state='اترفع', date=date, done=False)
    threading.Thread(target=process_upload, args=(store, jid, tmp, date), daemon=True).start()
    return redirect(url_for('job', jid=jid))


@app.route('/job/<jid>')
@auth
def job(jid):
    j = store.job_get(jid)
    if not j:
        abort(404)
    return render_template('job.html', j=j, jid=jid)


@app.route('/settings', methods=['POST'])
@auth
def save_settings():
    if request.form.get('reset'):
        s = dict(DEFAULTS)
    else:
        s = parse(request.form, load_settings())
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False), encoding='utf-8')
    return redirect(url_for('index', tab='settings', saved=1))


@app.route('/delete/<date>', methods=['POST'])
@auth
def delete(date):
    store.delete(date)
    return redirect(url_for('index', tab='sessions'))


@app.route('/health')
def health():
    return 'ok'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)), debug=False)
