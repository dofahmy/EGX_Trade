import os, json, uuid, threading, datetime as dt
from pathlib import Path
from functools import wraps

from flask import Flask, request, redirect, url_for, render_template, Response, abort

from engine import Store, process_upload, analyze, METHODS
import us

DATA_DIR = os.environ.get('DATA_DIR', '/data' if Path('/data').exists() else './data')
PASSWORD = os.environ.get('APP_PASSWORD', '')

BASE = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE / 'templates'))
# يدوّر على ملفات الصفحات جوه فولدر templates، ولو مش موجود يدوّر جنب app.py
from jinja2 import ChoiceLoader, FileSystemLoader
app.jinja_loader = ChoiceLoader([FileSystemLoader(str(BASE / 'templates')), FileSystemLoader(str(BASE))])
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024

# ---------- الأسواق ----------
# مصر بتفضل في نفس المكان القديم عشان الجلسات المرفوعة قبل كده ماتضيعش
MARKETS = {
    'egx': dict(title='البورصة المصرية', root=Path(DATA_DIR), windows=54),
    'us': dict(title='الأسهم الأمريكية (إس آند بي ٥٠٠)', root=Path(DATA_DIR) / 'us', windows=78),
}
STORES = {m: Store(v['root']) for m, v in MARKETS.items()}

DEFAULTS = {
    'egx': dict(day_win=20, min_prices=7, threshold=10, mult=2.0, min_win=20, max_win=None,
                min_share=10.0, gap=3.0, gap_max=None, hold=2, warn_share=2.0),
    # الجلسة الأمريكية ٧٨ فترة بدل ٥٤، فعدد المرات متكبّر بنفس النسبة. وعدد الأسهم أكبر، فالحد أكبر.
    # والسهم الأمريكي بيتداول على مئات الأسعار، فالنسبة اللي بتتلم عند سعر واحد صغيرة جدًا مقارنة بمصر
    'us': dict(day_win=29, min_prices=7, threshold=10, mult=2.0, min_win=29, max_win=None,
               min_share=0.0, gap=3.0, gap_max=None, hold=2, warn_share=0.2),
}
TYPES = dict(day_win=int, min_prices=int, threshold=int, mult=float, min_win=int, max_win=int,
             min_share=float, gap=float, gap_max=float, hold=int, warn_share=float)
OPTIONAL = {'max_win', 'gap_max'}


def market():
    m = request.values.get('m', 'egx')
    return m if m in MARKETS else 'egx'


def settings_file(m):
    return MARKETS[m]['root'] / 'settings.json'


def auth(f):
    @wraps(f)
    def w(*a, **k):
        if PASSWORD:
            a_ = request.authorization
            if not a_ or a_.password != PASSWORD:
                return Response('محتاج كلمة السر', 401, {'WWW-Authenticate': 'Basic realm="signals"'})
        return f(*a, **k)
    return w


def load_settings(m):
    s = dict(DEFAULTS[m])
    try:
        s.update(json.loads(settings_file(m).read_text(encoding='utf-8')))
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


def params(m):
    """الإعدادات المحفوظة، ولو فيه قيم في الرابط بتتطبق فوقها للتجربة من غير حفظ."""
    return parse(request.args, load_settings(m))


def cairo_today():
    return (dt.datetime.utcnow() + dt.timedelta(hours=2)).date().isoformat()


@app.context_processor
def helpers():
    m = market()
    def murl(endpoint, **kw):
        kw.setdefault('m', m)
        return url_for(endpoint, **kw)
    return dict(murl=murl, mkt=m, markets=MARKETS, mtitle=MARKETS[m]['title'], mwin=MARKETS[m]['windows'])


@app.route('/')
@auth
def index():
    m = market()
    store = STORES[m]
    p = params(m)
    days, trades, state, warns = analyze(store, **p)
    latest = days[-1] if days else None
    # كل إشارة مرة واحدة، ومعاها الطرق اللي طلّعتها
    merged = {}
    for k, tl in trades.items():
        for t in tl:
            x = merged.setdefault(t['signal_date'], dict(t, methods=[]))
            x['methods'].append(k)
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
    totals = {k: summary([t for t in closed if k in t['methods']]) for k in METHODS}
    tab = request.args.get('tab', 'today')
    extra = {}
    if m == 'us':
        extra = dict(us_date=us.last_us_session(), us_key=bool(os.environ.get('DATABENTO_API_KEY')),
                     us_dataset=us.DATASET, us_count=len(us.sp500()))
    return render_template('index.html', p=p, days=days, state=state, latest=latest,
                           today=cairo_today(), methods=METHODS, warns=warns,
                           open_sigs=open_sigs, closed=closed, totals=totals, tab=tab, **extra)


@app.route('/day/<date>')
@auth
def day(date):
    m = market()
    p = params(m)
    days, _, _, _ = analyze(STORES[m], **p)
    d = next((x for x in days if x['date'] == date), None)
    if not d:
        abort(404)
    return render_template('day.html', p=p, d=d)


@app.route('/upload', methods=['POST'])
@auth
def upload():
    store = STORES['egx']
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
    tmp = MARKETS['egx']['root'] / 'jobs' / f'{jid}{ext}'
    f.save(tmp)
    store.job_set(jid, state='اترفع', date=date, done=False)
    threading.Thread(target=process_upload, args=(store, jid, tmp, date), daemon=True).start()
    return redirect(url_for('job', jid=jid, m='egx'))


# ---------- الأسهم الأمريكية: حساب التكلفة وبعدين الجلب ----------

@app.route('/us/estimate', methods=['POST'])
@auth
def us_estimate():
    date = request.form.get('date') or us.last_us_session()
    try:
        dt.date.fromisoformat(date)
        cost, size = us.estimate(date)
        err = None
    except Exception as e:
        cost, size, err = None, None, str(e)[:500]
    return render_template('us_confirm.html', date=date, cost=cost, size=size, err=err,
                           dataset=us.DATASET, count=len(us.sp500()))


@app.route('/us/fetch', methods=['POST'])
@auth
def us_fetch():
    date = request.form.get('date')
    try:
        dt.date.fromisoformat(date)
    except Exception:
        return 'التاريخ مش مظبوط', 400
    store = STORES['us']
    jid = uuid.uuid4().hex[:10]
    store.job_set(jid, state='بدأ', date=date, done=False)
    threading.Thread(target=us.process_fetch, args=(store, jid, date), daemon=True).start()
    return redirect(url_for('job', jid=jid, m='us'))


@app.route('/job/<jid>')
@auth
def job(jid):
    j = STORES[market()].job_get(jid)
    if not j:
        abort(404)
    return render_template('job.html', j=j, jid=jid)


@app.route('/settings', methods=['POST'])
@auth
def save_settings():
    m = market()
    if request.form.get('reset'):
        s = dict(DEFAULTS[m])
    else:
        s = parse(request.form, load_settings(m))
    settings_file(m).write_text(json.dumps(s, ensure_ascii=False), encoding='utf-8')
    return redirect(url_for('index', m=m, tab='settings', saved=1))


@app.route('/delete/<date>', methods=['POST'])
@auth
def delete(date):
    m = market()
    STORES[m].delete(date)
    return redirect(url_for('index', m=m, tab='sessions'))


@app.route('/health')
def health():
    return 'ok'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)), debug=False)
