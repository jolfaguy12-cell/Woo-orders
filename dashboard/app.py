"""
Dashboard Flask application for the Behdashtik WooCommerce Orders Telegram service.

Runs on port 8000; exposed at https://support.behdashtik.ir/orders-api via nginx.
"""

import json
import logging
import os
import secrets
import socket
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import wraps

import requests
from dotenv import dotenv_values, set_key
from flask import (Blueprint, Flask, flash, jsonify, redirect, render_template,
                   request, send_file, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

# ---------------------------------------------------------------------------
# Bootstrap path so sibling modules are importable
# ---------------------------------------------------------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)

ENV_FILE = os.path.join(_PROJECT_ROOT, '.env')
DESTINATIONS_FILE = os.path.join(_PROJECT_ROOT, 'telegram_destinations.json')
WEBHOOK_LOG = os.path.join(_PROJECT_ROOT, 'data', 'webhook.log')
DASHBOARD_DATA = os.path.join(os.path.dirname(__file__), 'data')

import auth as _auth
import hub_client as _hub
import settings_manager as _sm
from pdf_generator import generate_pdf as _generate_pdf, find_invoice_pdf as _find_invoice_pdf

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

def _secret_key() -> str:
    key_file = os.path.join(DASHBOARD_DATA, 'secret_key.txt')
    os.makedirs(DASHBOARD_DATA, exist_ok=True)
    if os.path.exists(key_file):
        return open(key_file).read().strip()
    key = secrets.token_hex(32)
    with open(key_file, 'w') as f:
        f.write(key)
    return key


app = Flask(__name__, static_folder=None)
app.secret_key = _secret_key()
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

bp = Blueprint(
    'dashboard', __name__,
    url_prefix='/orders-api',
    static_folder='static',
    static_url_path='/static',
    template_folder='templates',
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env() -> dict:
    return dotenv_values(ENV_FILE)


def _mask(token: str) -> str:
    if not token or len(token) < 8:
        return '••••••••'
    return token[:4] + '••••••••' + token[-4:]


def _load_destinations() -> list:
    try:
        with open(DESTINATIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f).get('destinations', [])
    except Exception:
        return []


def _save_destinations(dests: list):
    with open(DESTINATIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump({'destinations': dests}, f, ensure_ascii=False, indent=2)


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


def _tail_log(n: int = 200) -> list[str]:
    if not os.path.exists(WEBHOOK_LOG):
        return []
    try:
        with open(WEBHOOK_LOG, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        return [l.rstrip() for l in lines[-n:]]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Auth decorators
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('dashboard.login'))
        if session.get('must_change_password') and request.endpoint != 'dashboard.change_password':
            return redirect(url_for('dashboard.change_password'))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'user' in session:
        return redirect(url_for('dashboard.index'))
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = _auth.verify_user(username, password)
        if user:
            session.permanent = True
            session['user'] = user['username']
            session['must_change_password'] = bool(user['must_change_password'])
            if user['must_change_password']:
                return redirect(url_for('dashboard.change_password'))
            return redirect(url_for('dashboard.index'))
        error = 'نام کاربری یا رمز عبور اشتباه است.'
    return render_template('login.html', error=error)


@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('dashboard.login'))


@bp.route('/change-password', methods=['GET', 'POST'])
def change_password():
    if 'user' not in session:
        return redirect(url_for('dashboard.login'))
    error = None
    if request.method == 'POST':
        pw1 = request.form.get('password', '')
        pw2 = request.form.get('password2', '')
        if len(pw1) < 8:
            error = 'رمز عبور باید حداقل ۸ کاراکتر باشد.'
        elif pw1 != pw2:
            error = 'رمزهای عبور با هم مطابقت ندارند.'
        else:
            _auth.change_password(session['user'], pw1)
            session['must_change_password'] = False
            flash('رمز عبور با موفقیت تغییر یافت.', 'success')
            return redirect(url_for('dashboard.index'))
    return render_template('change_password.html', error=error, force=session.get('must_change_password'))


# ---------------------------------------------------------------------------
# Dashboard overview
# ---------------------------------------------------------------------------

@bp.route('/')
@login_required
def index():
    cfg = _sm.load()
    hub_url = cfg['hub_url']
    hub_key = cfg['hub_api_key']

    webhook_up = _port_open(5100)
    hub_up = _port_open(8090)
    hub_info = _hub.health(hub_url, hub_key) if hub_up else {}

    env = _env()
    bot_configured = bool(env.get('TG_BOT_TOKEN', ''))

    recent_logs = _tail_log(50)

    return render_template(
        'index.html',
        webhook_up=webhook_up,
        hub_up=hub_up,
        hub_info=hub_info,
        bot_configured=bot_configured,
        recent_logs=recent_logs,
    )


# ---------------------------------------------------------------------------
# Telegram settings
# ---------------------------------------------------------------------------

@bp.route('/telegram', methods=['GET', 'POST'])
@login_required
def telegram():
    env = _env()
    cfg = _sm.load()
    success = None
    error = None

    if request.method == 'POST':
        action = request.form.get('action', '')

        if action == 'save_bot':
            new_token = request.form.get('bot_token', '').strip()
            new_admin = request.form.get('admin_id', '').strip()
            if new_token and not new_token.startswith('•'):
                set_key(ENV_FILE, 'TG_BOT_TOKEN', new_token)
            if new_admin:
                set_key(ENV_FILE, 'TG_ADMIN_ID', new_admin)
            success = 'تنظیمات بات تلگرام ذخیره شد.'
            env = _env()

        elif action == 'add_dest':
            chat_id = request.form.get('chat_id', '').strip()
            name = request.form.get('name', '').strip()
            dtype = request.form.get('type', 'user')
            if chat_id and name:
                dests = _load_destinations()
                if any(str(d['chat_id']) == chat_id for d in dests):
                    error = f'این chat_id قبلاً ثبت شده است.'
                else:
                    dests.append({'chat_id': chat_id, 'name': name, 'type': dtype})
                    _save_destinations(dests)
                    success = f'مقصد «{name}» اضافه شد.'
            else:
                error = 'chat_id و نام الزامی هستند.'

        elif action == 'remove_dest':
            chat_id = request.form.get('chat_id', '').strip()
            dests = _load_destinations()
            updated = [d for d in dests if str(d['chat_id']) != chat_id]
            if len(updated) < len(dests):
                _save_destinations(updated)
                success = 'مقصد حذف شد.'
            else:
                error = 'مقصدی با این chat_id یافت نشد.'

    destinations = _load_destinations()
    bot_token = env.get('TG_BOT_TOKEN', '')
    admin_id = env.get('TG_ADMIN_ID', '213946880')

    return render_template(
        'telegram.html',
        bot_token_masked=_mask(bot_token),
        admin_id=admin_id,
        destinations=destinations,
        success=success,
        error=error,
    )


@bp.route('/telegram/test', methods=['POST'])
@login_required
def telegram_test():
    env = _env()
    token = env.get('TG_BOT_TOKEN', '')
    if not token:
        return jsonify({'ok': False, 'error': 'TG_BOT_TOKEN پیکربندی نشده است.'})

    chat_id = request.json.get('chat_id', '') if request.is_json else ''
    text = request.json.get('text', '✅ پیام آزمایشی از پنل مدیریت بهداشتیک') if request.is_json else '✅ پیام آزمایشی'

    if not chat_id:
        dests = _load_destinations()
        if not dests:
            return jsonify({'ok': False, 'error': 'هیچ مقصدی تنظیم نشده است.'})
        chat_id = dests[0]['chat_id']

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={'chat_id': chat_id, 'text': text},
            timeout=10,
        )
        data = r.json()
        if data.get('ok'):
            return jsonify({'ok': True, 'message': f'پیام به {chat_id} ارسال شد.'})
        return jsonify({'ok': False, 'error': data.get('description', 'خطای ناشناخته')})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)})


# ---------------------------------------------------------------------------
# Message templates
# ---------------------------------------------------------------------------

@bp.route('/templates', methods=['GET', 'POST'])
@login_required
def templates_page():
    cfg = _sm.load()
    success = None

    if request.method == 'POST':
        for key in ('new_order', 'basalam_order', 'low_stock', 'out_of_stock', 'daily_report'):
            val = request.form.get(f'tpl_{key}', '')
            if val:
                cfg['templates'][key] = val
        cfg['send_pdf_with_new_order'] = (request.form.get('send_pdf_with_new_order') == '1')
        _sm.save(cfg)
        success = 'الگوهای پیام ذخیره شدند.'

    shortcodes = [
        ('{order_id}',               'شناسه سفارش'),
        ('{order_date}',             'تاریخ سفارش'),
        ('{status}',                 'کد وضعیت'),
        ('{status_label}',           'برچسب فارسی وضعیت'),
        ('{customer_name}',          'نام مشتری'),
        ('{phone}',                  'شماره تماس'),
        ('{email}',                  'ایمیل'),
        ('{total}',                  'مبلغ کل'),
        ('{payment_method}',         'روش پرداخت'),
        ('{shipping_method}',        'روش ارسال'),
        ('{address}',                'آدرس ارسال'),
        ('{items_list}',             'لیست محصولات سفارش'),
        ('{basalam_fee}',            'کارمزد باسلام'),
        ('{basalam_net}',            'مبلغ قابل دریافت از باسلام'),
        ('{basalam_purchase_count}', 'تعداد خرید مشتری از باسلام'),
        ('{product_name}',           'نام محصول (هشدار موجودی)'),
        ('{product_id}',             'شناسه محصول'),
        ('{sku}',                    'کد SKU'),
        ('{stock_quantity}',         'موجودی فعلی'),
        ('{stock_status}',           'وضعیت موجودی'),
    ]

    return render_template(
        'templates_page.html',
        cfg=cfg,
        shortcodes=shortcodes,
        success=success,
        send_pdf=cfg.get('send_pdf_with_new_order', True),
    )


# ---------------------------------------------------------------------------
# Order status settings
# ---------------------------------------------------------------------------

ALL_STATUSES = [
    ('processing',        'در حال پردازش',        False),
    ('pending',           'در انتظار پرداخت',      False),
    ('completed',         'تکمیل‌شده',              False),
    ('cancelled',         'لغو شده',               False),
    ('refunded',          'مسترد شده',             False),
    ('failed',            'ناموفق',                False),
    ('on-hold',           'در انتظار',             False),
    ('ready-to-ship',     'آماده ارسال',           False),
    ('bslm-preparation',  'باسلام — آماده‌سازی',   True),
    ('bslm-shipping',     'باسلام — ارسال شده',    True),
    ('bslm-completed',    'باسلام — تکمیل‌شده',    True),
    ('bslm-rejected',     'باسلام — لغو شده',      True),
    ('bslm-wait-vendor',  'باسلام — انتظار فروشنده', True),
]


@bp.route('/statuses', methods=['GET', 'POST'])
@login_required
def statuses():
    cfg = _sm.load()
    env = _env()
    success = None

    if request.method == 'POST':
        selected = request.form.getlist('statuses')
        basalam_only = request.form.get('basalam_only') == '1'
        new_val = ','.join(selected)
        cfg['target_order_statuses'] = new_val
        cfg['basalam_only'] = basalam_only
        _sm.save(cfg)
        set_key(ENV_FILE, 'TARGET_ORDER_STATUSES', new_val)
        success = 'وضعیت‌های هدف ذخیره شدند.'

    current_statuses = set(cfg['target_order_statuses'].split(','))

    return render_template(
        'statuses.html',
        all_statuses=ALL_STATUSES,
        current_statuses=current_statuses,
        basalam_only=cfg.get('basalam_only', True),
        success=success,
    )


# ---------------------------------------------------------------------------
# Stock alerts
# ---------------------------------------------------------------------------

@bp.route('/stock', methods=['GET', 'POST'])
@login_required
def stock():
    cfg = _sm.load()
    success = None
    error = None

    if request.method == 'POST':
        sa = cfg.setdefault('stock_alerts', {})
        sa['low_stock_enabled'] = request.form.get('low_stock_enabled') == '1'
        sa['out_of_stock_enabled'] = request.form.get('out_of_stock_enabled') == '1'
        try:
            sa['low_stock_threshold'] = int(request.form.get('threshold', 5))
        except ValueError:
            error = 'آستانه موجودی باید عدد صحیح باشد.'
        if not error:
            _sm.save(cfg)
            success = 'تنظیمات هشدار موجودی ذخیره شد.'

    hub_url = cfg['hub_url']
    hub_key = cfg['hub_api_key']
    threshold = cfg.get('stock_alerts', {}).get('low_stock_threshold', 5)

    # Fetch low-stock products from Hub for preview
    low_stock_products = None
    if _port_open(8090):
        result = _hub.list_products(hub_url, hub_key, per_page=10, stock_status='outofstock')
        if result and 'data' in result:
            low_stock_products = result['data']

    return render_template(
        'stock.html',
        cfg=cfg,
        low_stock_products=low_stock_products,
        threshold=threshold,
        success=success,
        error=error,
    )


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@bp.route('/logs')
@login_required
def logs():
    lines = _tail_log(500)
    return render_template('logs.html', lines=lines, log_path=WEBHOOK_LOG)


@bp.route('/api/logs')
@login_required
def api_logs():
    n = min(int(request.args.get('n', 100)), 500)
    lines = _tail_log(n)
    return jsonify({'lines': lines, 'count': len(lines)})


# ---------------------------------------------------------------------------
# Recent orders
# ---------------------------------------------------------------------------

@bp.route('/orders')
@login_required
def orders():
    cfg = _sm.load()
    hub_url = cfg['hub_url']
    hub_key = cfg['hub_api_key']

    page = int(request.args.get('page', 1))
    status_filter = request.args.get('status', '')
    source_filter = request.args.get('source', '')
    search_q = request.args.get('search', '')

    orders_data = None
    hub_up = _port_open(8090)
    if hub_up:
        orders_data = _hub.list_orders(
            hub_url, hub_key,
            page=page, per_page=25,
            status=status_filter or None,
            order_source=source_filter or None,
            search=search_q or None,
        )

    status_labels = {slug: label for slug, label, _ in ALL_STATUSES}

    return render_template(
        'orders.html',
        orders_data=orders_data,
        hub_up=hub_up,
        page=page,
        status_filter=status_filter,
        source_filter=source_filter,
        search_q=search_q,
        all_statuses=ALL_STATUSES,
        status_labels=status_labels,
    )


# ---------------------------------------------------------------------------
# General settings
# ---------------------------------------------------------------------------

@bp.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    cfg = _sm.load()
    env = _env()
    success = None

    if request.method == 'POST':
        action = request.form.get('action', 'save')

        if action == 'save_hub':
            cfg['hub_url'] = request.form.get('hub_url', cfg['hub_url']).strip()
            new_key = request.form.get('hub_api_key', '').strip()
            if new_key and not new_key.startswith('•'):
                cfg['hub_api_key'] = new_key
            _sm.save(cfg)
            success = 'تنظیمات هاب ذخیره شد.'

        elif action == 'save_store':
            store_name = request.form.get('store_name', '').strip()
            store_phone = request.form.get('store_phone', '').strip()
            store_address = request.form.get('store_address', '').strip()
            if store_name:
                set_key(ENV_FILE, 'STORE_NAME', store_name)
                os.environ['STORE_NAME'] = store_name
            if store_phone:
                set_key(ENV_FILE, 'STORE_PHONE', store_phone)
                os.environ['STORE_PHONE'] = store_phone
            if store_address:
                set_key(ENV_FILE, 'STORE_ADDRESS', store_address)
                os.environ['STORE_ADDRESS'] = store_address
            # Clear cached PDFs so next generation picks up new store info
            try:
                from pdf_generator import cleanup_old_invoices as _cleanup
                _cleanup(max_age_days=0)
            except Exception:
                pass
            success = 'اطلاعات فروشگاه ذخیره شد. کش فاکتورها پاک شد.'
            env = _env()

        elif action == 'save_webhook':
            new_secret = request.form.get('webhook_secret', '').strip()
            new_port = request.form.get('webhook_port', '').strip()
            if new_secret and not new_secret.startswith('•'):
                set_key(ENV_FILE, 'WEBHOOK_SECRET', new_secret)
            if new_port and new_port.isdigit():
                set_key(ENV_FILE, 'WEBHOOK_PORT', new_port)
            success = 'تنظیمات Webhook ذخیره شد.'
            env = _env()

        elif action == 'save_daily_report':
            dr = cfg.get('daily_report', {})
            dr['enabled'] = request.form.get('dr_enabled') == '1'
            dr['send_time'] = request.form.get('dr_send_time', '23:55').strip() or '23:55'
            cfg['daily_report'] = dr
            _sm.save(cfg)
            # Reschedule if scheduler is running
            try:
                from scheduler import reschedule
                reschedule(dr['send_time'])
            except Exception:
                pass
            success = 'تنظیمات گزارش روزانه ذخیره شد.'

        elif action == 'generate_invoice_key':
            cfg['invoice_api_key'] = secrets.token_hex(32)
            _sm.save(cfg)
            success = 'کلید API فاکتور جدید ایجاد شد.'

        elif action == 'save_manager_ids':
            raw = request.form.get('manager_ids', '')
            ids = [int(x.strip()) for x in raw.splitlines() if x.strip().lstrip('-').isdigit()]
            cfg['telegram_manager_ids'] = ids
            _sm.save(cfg)
            success = f'شناسه‌های مجاز ذخیره شد ({len(ids)} مورد).'

    manager_ids_text = '\n'.join(str(x) for x in cfg.get('telegram_manager_ids', []))
    return render_template(
        'settings.html',
        cfg=cfg,
        env=env,
        hub_api_key_masked=_mask(cfg.get('hub_api_key', '')),
        webhook_secret_masked=_mask(env.get('WEBHOOK_SECRET', '')),
        invoice_api_key_masked=_mask(cfg.get('invoice_api_key', '')),
        manager_ids_text=manager_ids_text,
        success=success,
    )


# ---------------------------------------------------------------------------
# API: daily orders chart data
# ---------------------------------------------------------------------------

_EXCLUDE_STATUSES = {'pending', 'cancelled', 'bslm-rejected'}


@bp.route('/api/daily-orders')
@login_required
def api_daily_orders():
    cfg = _sm.load()
    hub_url = cfg['hub_url']
    hub_key = cfg['hub_api_key']

    days = min(int(request.args.get('days', 30)), 90)
    date_after = (date.today() - timedelta(days=days)).isoformat()

    # Fetch all pages of orders in the date range
    all_orders = []
    if _port_open(8090):
        page = 1
        while True:
            result = _hub.list_orders(
                hub_url, hub_key,
                page=page, per_page=100,
                date_after=date_after,
            )
            if not result or 'error' in result or 'data' not in result:
                break
            all_orders.extend(result['data'])
            pg = result.get('pagination', {})
            if page >= pg.get('pages', 1):
                break
            page += 1

    # Aggregate by day, excluding pending + cancelled
    by_day: dict[str, int] = defaultdict(int)
    by_day_website: dict[str, int] = defaultdict(int)
    by_day_basalam: dict[str, int] = defaultdict(int)

    for o in all_orders:
        if o.get('status') in _EXCLUDE_STATUSES:
            continue
        day = (o.get('date_created') or '')[:10]
        if not day:
            continue
        by_day[day] += 1
        src = o.get('order_source', '')
        if src == 'website':
            by_day_website[day] += 1
        elif src == 'basalam':
            by_day_basalam[day] += 1

    # Build full date range with zeros for missing days
    today = date.today()
    labels, all_counts, website_counts, basalam_counts = [], [], [], []
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        labels.append(d)
        all_counts.append(by_day.get(d, 0))
        website_counts.append(by_day_website.get(d, 0))
        basalam_counts.append(by_day_basalam.get(d, 0))

    return jsonify({
        'labels': labels,
        'all': all_counts,
        'website': website_counts,
        'basalam': basalam_counts,
        'hub_up': _port_open(8090),
    })


# ---------------------------------------------------------------------------
# API: today's KPI cards
# ---------------------------------------------------------------------------

@bp.route('/api/today-kpi')
@login_required
def api_today_kpi():
    from zoneinfo import ZoneInfo
    cfg = _sm.load()

    if not _port_open(8090):
        return jsonify({'hub_up': False})

    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from daily_report import compute_today_kpis
        kpis = compute_today_kpis(cfg['hub_url'], cfg['hub_api_key'])
        return jsonify(kpis)
    except Exception as exc:
        _log.error("api_today_kpi error: %s", exc)
        return jsonify({'hub_up': True, 'error': str(exc)}), 500


# ---------------------------------------------------------------------------
# API: test-send daily Telegram report
# ---------------------------------------------------------------------------

@bp.route('/api/daily-report/test', methods=['POST'])
@login_required
def api_daily_report_test():
    cfg = _sm.load()
    env = _env()
    bot_token = env.get('TG_BOT_TOKEN', '')

    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from daily_report import send_daily_report
        result = send_daily_report(
            hub_url=cfg['hub_url'],
            hub_key=cfg['hub_api_key'],
            bot_token=bot_token,
            destinations_file=DESTINATIONS_FILE,
        )
        return jsonify(result)
    except Exception as exc:
        _log.error("daily_report test failed: %s", exc)
        return jsonify({'ok': False, 'error': str(exc)}), 500


# ---------------------------------------------------------------------------
# API: scheduler status
# ---------------------------------------------------------------------------

@bp.route('/api/scheduler-status')
@login_required
def api_scheduler_status():
    try:
        from scheduler import get_next_fire_time
        return jsonify({'next_fire': get_next_fire_time()})
    except Exception:
        return jsonify({'next_fire': None})


# ---------------------------------------------------------------------------
# API: status JSON for AJAX dashboard refresh
# ---------------------------------------------------------------------------

@bp.route('/api/status')
@login_required
def api_status():
    cfg = _sm.load()
    return jsonify({
        'webhook_up': _port_open(5100),
        'hub_up': _port_open(8090),
        'bot_configured': bool(_env().get('TG_BOT_TOKEN', '')),
        'destinations': len(_load_destinations()),
        'timestamp': datetime.utcnow().isoformat(),
    })


# ---------------------------------------------------------------------------
# External Invoice API (no session auth — uses X-Invoice-API-Key header)
# ---------------------------------------------------------------------------

@bp.route('/api/v1/invoice/<int:order_id>')
def invoice_api(order_id: int):
    req_key = request.headers.get('X-Invoice-API-Key', '')
    cfg = _sm.load()
    stored_key = cfg.get('invoice_api_key', '')

    if not stored_key or req_key != stored_key:
        return jsonify({'error': 'unauthorized', 'code': 401}), 401

    hub_url = cfg['hub_url']
    hub_key = cfg['hub_api_key']

    try:
        # Serve cached PDF if available (avoids regeneration on every call)
        pdf_path = _find_invoice_pdf(order_id)
        if not pdf_path:
            order = _hub.get_order_detail(hub_url, hub_key, order_id)
            if not order:
                return jsonify({'error': 'order_not_found', 'code': 404}), 404
            # Generate and persist so subsequent calls are served from cache
            pdf_path = _generate_pdf(order)

        return send_file(
            pdf_path,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'invoice_{order_id}.pdf',
        )
    except Exception as exc:
        _log.error("invoice_api error for order %s: %s", order_id, exc)
        return jsonify({'error': 'internal_error', 'code': 500}), 500


# ---------------------------------------------------------------------------
# External Tracking API (same X-Invoice-API-Key auth)
# ---------------------------------------------------------------------------

@bp.route('/api/v1/tracking/<int:order_id>')
def tracking_api(order_id: int):
    req_key = request.headers.get('X-Invoice-API-Key', '')
    cfg = _sm.load()
    stored_key = cfg.get('invoice_api_key', '')

    if not stored_key or req_key != stored_key:
        return jsonify({'error': 'unauthorized', 'code': 401}), 401

    hub_url = cfg['hub_url']
    hub_key = cfg['hub_api_key']

    try:
        order = _hub.get_order_detail(hub_url, hub_key, order_id)
        if not order:
            return jsonify({'error': 'order_not_found', 'code': 404}), 404
        tracking = _hub.extract_tracking(order)
        return jsonify({'ok': True, 'data': tracking})
    except Exception as exc:
        _log.error("tracking_api error for order %s: %s", order_id, exc)
        return jsonify({'error': 'internal_error', 'code': 500}), 500


# ---------------------------------------------------------------------------
# App init & run
# ---------------------------------------------------------------------------

app.register_blueprint(bp)

if __name__ == '__main__':
    _auth.init_auth()
    port = int(os.getenv('DASHBOARD_PORT', '8000'))
    _log.info("Dashboard starting on port %d", port)
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
