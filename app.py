import os, uuid, threading, datetime as dt
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


def params():
    def g(k, d, t=int):
        try:
            return t(request.args.get(k, d))
        except Exception:
            return d
    return dict(min_win=g('min_win', 20), min_prices=g('min_prices', 7),
                threshold=g('threshold', 10), hold=g('hold', 2), mult=g('mult', 2.0, float))


def cairo_today():
    return (dt.datetime.utcnow() + dt.timedelta(hours=2)).date().isoformat()


@app.route('/')
@auth
def index():
    p = params()
    days, trades, state = analyze(store, **p)
    latest = days[-1] if days else None
    return render_template('index.html', p=p, days=days, trades=trades, state=state,
                           latest=latest, today=cairo_today(), methods=METHODS)


@app.route('/day/<date>')
@auth
def day(date):
    p = params()
    days, _, _ = analyze(store, **p)
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


@app.route('/delete/<date>', methods=['POST'])
@auth
def delete(date):
    store.delete(date)
    return redirect(url_for('index'))


@app.route('/health')
def health():
    return 'ok'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)), debug=False)
