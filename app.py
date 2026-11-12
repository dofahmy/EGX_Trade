import os, json, uuid, threading, tempfile, datetime as dt
import engine
import pandas as pd
from pathlib import Path
from functools import wraps

from flask import jsonify, Flask, request, redirect, url_for, render_template, Response, abort

from engine import Store, process_upload, analyze, METHODS, same_day_trades, bounce_days
import us
import candles
import flowsig
import groupsig
import bigsell

DATA_DIR = os.environ.get('DATA_DIR', '/data' if Path('/data').exists() else './data')
PASSWORD = os.environ.get('APP_PASSWORD', '')

BASE = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE / 'templates'))


@app.template_filter('num')
def num_filter(v):
    """رقم من غير .0 لو صحيح."""
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else ('%g' % f)
    except Exception:
        return v
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
                min_share=10.0, gap=3.0, gap_max=None, hold=2, warn_share=2.0, tp=3.0, picks=2, exclude='INEG', b_drop=2.5, b_range=7.0, b_val=0.5, b_min=30, b_maxdrop=10.0,
                c_share=10.0, c_ratio=4.0, c_val=1.0, c_near=3.0, c_wall=2.0, c_zone=0.0, c_share_max=None, c_ratio_max=None),
    # الجلسة الأمريكية ٧٨ فترة بدل ٥٤، فعدد المرات متكبّر بنفس النسبة. وعدد الأسهم أكبر، فالحد أكبر.
    # والسهم الأمريكي بيتداول على مئات الأسعار، فالنسبة اللي بتتلم عند سعر واحد صغيرة جدًا مقارنة بمصر
    'us': dict(day_win=29, min_prices=7, threshold=10, mult=2.0, min_win=29, max_win=None,
               min_share=0.0, gap=3.0, gap_max=None, hold=2, warn_share=0.2, tp=3.0, picks=2, exclude='', b_drop=2.5, b_range=7.0, b_val=0.5, b_min=30, b_maxdrop=10.0,
               c_share=5.0, c_ratio=4.0, c_val=1.0, c_near=3.0, c_wall=2.0, c_zone=0.1, c_share_max=None, c_ratio_max=None),
}
TYPES = dict(day_win=int, min_prices=int, threshold=int, mult=float, min_win=int, max_win=int,
             min_share=float, gap=float, gap_max=float, hold=int, warn_share=float, tp=float, picks=int, exclude=str, b_drop=float, b_range=float, b_val=float, b_min=int, b_maxdrop=float,
             c_share=float, c_ratio=float, c_val=float, c_near=float, c_wall=float, c_zone=float, c_share_max=float, c_ratio_max=float)
OPTIONAL = {'max_win', 'gap_max', 'exclude', 'b_maxdrop', 'c_share_max', 'c_ratio_max'}


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


def norm_codes(v):
    """أكواد مفصولة بمسافة أو فاصلة أو سطر جديد، بنرجعها مرتبة ومن غير تكرار."""
    import re
    return ' '.join(sorted({c.upper() for c in re.split(r'[\s,،;]+', v or '') if c}))


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
            out[k] = t(float(v)) if t is int else norm_codes(v) if k == 'exclude' else t(v)
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
    if ext not in ('.xlsx', '.xlsm', '.rar', '.zip', '.csv'):
        return 'الملف لازم يكون إكسل أو csv أو مضغوط', 400
    jid = uuid.uuid4().hex[:10]
    tmp = MARKETS['egx']['root'] / 'jobs' / f'{jid}{ext}'
    f.save(tmp)
    store.job_set(jid, state='اترفع', date=date, done=False)
    # ملفات سيجما فيها التاريخ جواها، وممكن تبقى أيام كتير في ملف مضغوط واحد
    with tempfile.TemporaryDirectory() as td:
        sig = ext == '.csv' or (ext in ('.zip', '.rar') and engine.is_sigma(tmp, Path(td)))
    if sig:
        threading.Thread(target=engine.process_sigma, args=(store, jid, tmp), daemon=True).start()
    else:
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


@app.route('/us/bars_estimate', methods=['POST'])
@auth
def us_bars_estimate():
    """تكلفة تكملة حركة اليوم لأسهم القاعدة بس."""
    date = request.form.get('date')
    syms = request.form.get('syms', '').split()
    try:
        dt.date.fromisoformat(date)
        cost, size = us.estimate(date, syms)
        err = None
    except Exception as e:
        cost, size, err = None, None, str(e)[:500]
    return render_template('us_confirm.html', date=date, cost=cost, size=size, err=err,
                           dataset=us.DATASET, count=len(syms), syms=syms)


@app.route('/us/bars', methods=['POST'])
@auth
def us_bars():
    date = request.form.get('date')
    syms = request.form.get('syms', '').split()
    try:
        dt.date.fromisoformat(date)
    except Exception:
        return 'التاريخ مش مظبوط', 400
    if not syms:
        return 'مفيش أسهم', 400
    store = STORES['us']
    jid = uuid.uuid4().hex[:10]
    store.job_set(jid, state='بدأ', date=date, done=False)
    threading.Thread(target=us.process_bars, args=(store, jid, date, syms), daemon=True).start()
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
        # الرجوع للأصل مابيمسحش قايمة الأسهم المستبعدة
        s = dict(DEFAULTS[m], exclude=load_settings(m).get('exclude'))
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


@app.route('/stock/<sym>')
@auth
def stock(sym):
    """كل جلسات سهم واحد: الأسعار، وأقوى ٣ مستويات، وحالته في الإشارات."""
    m = market()
    store = STORES[m]
    p = params(m)
    days, _, _, _ = analyze(store, **p)
    rows = []
    for i, d in enumerate(days):
        st, lv, _ = store.load(d['date'])
        if sym not in st.index:
            continue
        r = st.loc[sym]
        top = lv[lv['sym'] == sym].sort_values(['win', 'sell_vol'], ascending=False).head(3)
        a_ = d['allsig']
        status = a_.loc[a_['sym'] == sym, 'status'].iloc[0] if (a_['sym'] == sym).any() else None
        nxt = days[i + 1]['date'] if i + 1 < len(days) else None
        nopen = None
        if nxt:
            s2 = store.load(nxt)[0]
            if sym in s2.index:
                nopen = float(s2.loc[sym, 'open'])
        rows.append(dict(date=d['date'], name=r['name'], prev=r['prev'], open=r['open'], lo=r['lo'], hi=r['hi'],
                         last=r['last'], chg=r['chg'], n=int(r['n']), val_m=r['val_m'], nprices=int(r['nprices']),
                         levels=top.to_dict('records'), status=status, day_count=d['count'],
                         trig=d['trig'], nopen=nopen))
    if not rows:
        abort(404)
    return render_template('stock.html', sym=sym, rows=rows, p=p)


@app.route('/chart/<sym>')
@auth
def chart(sym):
    """شموع السهم ملونة بحالة الحيطة، وعرضها بقوتها، والشمعة الدهبية والسودا بإطار."""
    m = market()
    sym = sym.upper()
    data = candles.series(STORES[m], sym, **params(m))
    if not data:
        abort(404)
    name = STORES[m].load(data[-1]['d'])[0].loc[sym, 'name']
    return render_template('chart.html', sym=sym, name=name, data=data)


@app.route('/h11')
@auth
def h11_page():
    store = STORES['egx']
    n = request.args.get('n', '60')
    n = int(n) if n.isdigit() else 0
    try:
        days, summ = flowsig.h11(store, noon_table())
    except Exception:
        app.logger.exception('h11')
        days, summ = [], {}
    return render_template('h11.html', days=days[:n] if n else days, summ=summ, n=n)


def noon_table():
    """آخر ملف ساعة ١١ اترفع (لو لسه الجلسة الكاملة ماترفعتش)."""
    d = MARKETS['egx']['root'] / 'noon'
    fs = sorted(d.glob('*.csv')) if d.exists() else []
    if not fs:
        return None
    t = pd.read_csv(fs[-1], dtype={'sym': str})
    t['date'] = fs[-1].stem
    return t


@app.route('/upload_noon', methods=['POST'])
@auth
def upload_noon():
    """ملف صفقات الساعة الأولى من البرنامج اللي على الجهاز: بنحسب منه الساعة الأولى لكل سهم ونخزنه."""
    store = STORES['egx']
    f = request.files.get('file')
    if not f:
        return 'مفيش ملف', 400
    out = MARKETS['egx']['root'] / 'noon'
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / ('x' + (Path(f.filename or '').suffix.lower() or '.csv'))
        f.save(p)
        got = engine.sigma_files(p, Path(td))
        if not got:
            return 'الملف مش ملف صفقات سيجما', 400
        date, csv = sorted(got.items())[-1] if isinstance(got, dict) else got[-1]
        before = [x for x in store.dates() if x < date]
        prev = store.load(before[-1])[0]['last'].to_dict() if before else {}
        df = engine.read_sigma(csv, prev, engine.sigma_names())
        st = engine.day_stats(df)
    kind = request.form.get('kind') or request.args.get('kind') or 'noon'
    if kind == 'm15':
        out = MARKETS['egx']['root'] / 'm15'
        out.mkdir(parents=True, exist_ok=True)
        st.reset_index()[['sym', 'name', 'open', 'p15', 'val15']].to_csv(out / f'{date}.csv', index=False)
    else:
        st.reset_index()[['sym', 'name', 'open', 'h1vol', 'h1b', 'h1s', 'p11', 'h1val']].to_csv(out / f'{date}.csv', index=False)
    return 'تمام', 200


def m15_table():
    d = MARKETS['egx']['root'] / 'm15'
    fs = sorted(d.glob('*.csv')) if d.exists() else []
    if not fs:
        return None
    t = pd.read_csv(fs[-1], dtype={'sym': str})
    t['date'] = fs[-1].stem
    return t


@app.route('/m15')
@auth
def m15_page():
    n = request.args.get('n', '20')
    n = int(n) if n.isdigit() else 0
    try:
        days, summ = flowsig.m15(STORES['egx'], m15_table())
    except Exception:
        app.logger.exception('m15')
        days, summ = [], {}
    return render_template('m15.html', days=days[:n] if n else days, summ=summ, n=n)


@app.route('/bigsell')
@auth
def bigsell_page():
    n = request.args.get('n', '8')
    n = int(n) if n.isdigit() else 0
    try:
        weeks, summ = bigsell.page(STORES['egx'], n)
    except Exception:
        app.logger.exception('bigsell')
        weeks, summ = [], {}
    return render_template('bigsell.html', weeks=weeks, summ=summ, n=n)


@app.route('/groups')
@auth
def groups_page():
    m = market()
    weeks = request.args.get('w', '12')
    weeks = int(weeks) if weeks.isdigit() else 0
    end = request.args.get('to') or None
    try:
        gs, labels, ends = groupsig.weekly(STORES[m], weeks, end)
    except Exception:
        app.logger.exception('groups')
        gs, labels, ends = [], [], []
    return render_template('groups.html', groups=gs, labels=labels, ends=ends, weeks=weeks, end=end or (ends[-1] if ends else ''))


@app.route('/candles')
@auth
def candles_page():
    """الشموع: قايمة كل الأسهم على اليمين، والسهم اللي تدوسي عليه بيترسم على الشمال مع إشارة الساعة الأولى."""
    m = market()
    store = STORES[m]
    dates = store.dates()
    stocks, mk = [], None
    if dates:
        st = store.load(dates[-1])[0]
        try:
            last_sig = flowsig.last_signals(store)
            mt = flowsig.market(store)
        except Exception:
            app.logger.exception('flowsig')
            last_sig, mt = {}, None
        recent = set(dates[-10:])
        for sym, r in st.sort_values('val_m', ascending=False).iterrows():
            ls = last_sig.get(sym)
            stocks.append(dict(sym=sym, name=str(r['name']), last=float(r['last']),
                               chg=round(float(r['chg']), 2) if pd.notna(r['chg']) else None,
                               sig=ls if ls in recent else None))
        if mt is not None and len(mt):
            d = mt.index[-1]
            row = mt.loc[d]
            top = []
            if bool(row['cluster']):
                t = flowsig.table(store)[0]
                top = t[(t['date'] == d) & t['buy']].sort_values('g12', ascending=False)['sym'].head(3).tolist()
            mk = dict(d=d, nb=int(row['nb']), ns=int(row['ns']), net=int(row['net']), dd=round(float(row['dd']), 1),
                      cluster=bool(row['cluster']), top=top, h1=bool(row['h1']))
    sel = (request.args.get('sym') or (stocks[0]['sym'] if stocks else '')).upper()
    return render_template('candles.html', stocks=stocks, sel=sel, last_date=dates[-1] if dates else None, mk=mk)


@app.route('/candles/data/<sym>')
@auth
def candles_data(sym):
    m = market()
    sym = sym.upper()
    try:
        data = flowsig.stock(STORES[m], sym)
    except Exception as e:
        app.logger.exception('candles_data %s', sym)
        return jsonify(sym=sym, data=None, error=str(e)[:200]), 500
    return jsonify(sym=sym, data=data)


@app.route('/trade')
@auth
def trade():
    """التريدة: شراء وبيع في نفس اليوم على السهم صاحب أكبر نسبة اتلمت."""
    return _trade('trade.html')


@app.route('/t')
@auth
def trade_solo():
    """صفحة التريدة لوحدها: الجدول بس، من غير أي لينكات."""
    return _trade('trade_solo.html')


def _trade(tpl):
    m = market()
    p = params(m)
    rows = same_day_trades(STORES[m], **p)
    # القاع: يوم مفيش فيه ولا سهم من الإشارات إقفاله موجب.
    # الأسهم المنتقاه: أسهم أول يوم بعد القاع فيه أسهم، وبعده بنستنى لحد القاع الجاي.
    waiting = False
    for r in rows:
        r['bottom'] = not r['stocks']
        r['selected'] = bool(r['stocks']) and waiting
        if r['bottom']:
            waiting = True
        elif r['selected']:
            waiting = False
    shown = [r for r in rows if r['bottom'] or r['selected']]
    # أعلى عائد وصله السهم من يوم الشراء لحد آخر جلسة، والعائد لحد آخر جلسة
    st_all = STORES[m]
    dates = st_all.dates()
    cache = {}
    def stats(d):
        if d not in cache:
            cache[d] = st_all.load(d)[0]
        return cache[d]
    for r in shown:
        for it in r['stocks']:
            n = it['next']
            if not n or n.get('missing'):
                continue
            sym, buy = it['pick']['sym'], n['buy']
            best, best_d, now, now_d = None, None, None, None
            for d in dates[dates.index(n['date']):]:
                s = stats(d)
                if sym not in s.index:
                    continue
                hi, last = float(s.loc[sym, 'hi']), float(s.loc[sym, 'last'])
                if best is None or hi > best:
                    best, best_d = hi, d
                now, now_d = last, d
            if best is not None:
                n['max_ret'] = round((best / buy - 1) * 100, 2)
                n['max_date'] = best_d
                n['now_ret'] = round((now / buy - 1) * 100, 2)
                n['now_date'] = now_d
                n['now_last'] = now
    state = None
    if rows:
        if waiting:
            state = 'bottom'   # آخر حاجة حصلت قاع، ومستنيين أول أسهم
        elif rows[-1]['selected'] and not rows[-1]['next_date']:
            state = 'buy'
        else:
            state = 'wait'
    last_bottom = next((r['date'] for r in reversed(rows) if r['bottom']), None)

    used = [r for r in rows if r['selected']]
    done = [it['next'] for r in used for it in r['stocks'] if it['next'] and not it['next'].get('missing')]
    sm = {}
    for key in ('res', 'oc'):
        if done:
            v = [n[key] for n in done]
            sm[key] = dict(n=len(v), avg=round(sum(v) / len(v), 2), total=round(sum(v), 2),
                           wins=sum(1 for x in v if x > 0), losses=sum(1 for x in v if x < 0))
    if done:
        sm['res']['tp'] = sum(1 for n in done if n['why'] == 'tp')
    for key in ('max_ret', 'now_ret'):
        v = [n[key] for n in done if key in n]
        if v:
            sm[key] = dict(n=len(v), avg=round(sum(v) / len(v), 2), total=round(sum(v), 2),
                           wins=sum(1 for x in v if x > 0), losses=sum(1 for x in v if x < 0))
    return render_template(tpl, rows=shown, allrows=rows, p=p, sm=sm, state=state,
                           last_bottom=last_bottom, n_bottom=sum(r['bottom'] for r in rows),
                           n_sel=sum(r['selected'] for r in rows))


@app.route('/bounce')
@auth
def bounce():
    """قاعدة الارتداد: الأسهم اللي نزلت يومين ورا بعض بقوة، وأداؤها الجلسة اللي بعدها مقارنة بالسوق."""
    m = market()
    p = params(m)
    rows = bounce_days(STORES[m], **p)
    # الأسهم المختارة من القايمتين في كل يوم، من غير تكرار
    for r in rows:
        seen, picks = set(), []
        for l in r.get('lists', []):
            for x in l['items']:
                if x['sym'] not in seen:
                    seen.add(x['sym'])
                    picks.append(dict(x, why=l['key']))
        # لو القاعدة طلّعت أسهم أقل من الحد، مفيش شراء اليوم ده
        r['few'] = 0 < len(r['rows']) < p['b_min']
        r['picks'] = [] if r['few'] else picks
        r['need_bars'] = bool(r.get('missing_bars'))
        res = [x['next'] for x in r['picks'] if 'next' in x]
        r['p_avg'] = round(sum(res) / len(res), 2) if res else None
        r['p_hit'] = (r['p_avg'] > r['mk_med']) if (res and r.get('mk_med') is not None) else None
    done = [r for r in rows if r['p_hit'] is not None]
    sm = None
    if done:
        allp = [x['next'] for r in done for x in r['picks'] if 'next' in x]
        sm = dict(days=len(done), hit=sum(1 for r in done if r['p_hit']),
                  p_avg=round(sum(allp) / len(allp), 2), n=len(allp),
                  wins=sum(1 for x in allp if x > 0), g10=sum(1 for x in allp if x >= 10),
                  mk=round(sum(r['mk_med'] for r in done) / len(done), 2))
    return render_template('bounce.html', rows=rows, p=p, sm=sm)


@app.route('/exclude', methods=['POST'])
@auth
def exclude():
    """استبعاد سهم من التريدة بزرار من الجدول."""
    m = market()
    s = load_settings(m)
    s['exclude'] = norm_codes((s.get('exclude') or '') + ' ' + request.form.get('sym', ''))
    settings_file(m).write_text(json.dumps(s, ensure_ascii=False), encoding='utf-8')
    return redirect(url_for('trade', m=m))


@app.route('/export')
@auth
def export():
    """ملف مضغوط فيه ملخص كل الجلسات (مش الصفقات نفسها)، عشان التحليل برا البرنامج."""
    import io, zipfile
    m = market()
    root = MARKETS[m]['root'] / 'sessions'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(root.rglob('*')):
            if f.is_file():
                z.write(f, f.relative_to(root.parent))
        sf = settings_file(m)
        if sf.exists():
            z.write(sf, 'settings.json')
    buf.seek(0)
    from flask import send_file
    return send_file(buf, mimetype='application/zip', as_attachment=True,
                     download_name=f'sessions_{m}_{dt.date.today().isoformat()}.zip')


@app.route('/health')
def health():
    return 'ok'


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8000)), debug=False)
