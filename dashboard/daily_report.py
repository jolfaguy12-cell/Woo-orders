"""
Daily KPI computation and Telegram report for Behdashtik Orders.

Flow:
  compute_today_kpis()  →  format_telegram_report()  →  send_daily_report()

All data comes from the Behdashtik Hub API (port 8090).
No direct WordPress/WooCommerce connections.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

_log = logging.getLogger(__name__)
_TZ = ZoneInfo('Asia/Tehran')

_QOM_NAMES = {'قم', 'قُم', 'Qom', 'qom', 'QOM'}
_CANCELLED_STATUSES = {'cancelled', 'bslm-rejected'}
_EXCLUDED_STATUSES = {'pending'} | _CANCELLED_STATUSES
_SHIPPED_STATUSES = {'completed', 'bslm-shipping'}
_FREE_SHIP_THRESHOLD = 2_000_000   # Toman


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today_tehran() -> str:
    return datetime.now(_TZ).date().isoformat()


def _fmt_money(amount: float) -> str:
    return f"{int(amount):,} تومان"


def _fmt_num(n: int) -> str:
    return f"{n:,}"


def _is_qom(order: dict) -> bool:
    city = (
        (order.get('billing') or {}).get('city') or
        (order.get('shipping') or {}).get('city') or ''
    ).strip()
    return city in _QOM_NAMES or 'قم' in city


# ---------------------------------------------------------------------------
# KPI computation
# ---------------------------------------------------------------------------

def _fetch_all_pages(hub_url: str, hub_key: str, **kwargs) -> list:
    """Iterate paginated Hub list_orders calls and return all rows."""
    from hub_client import list_orders
    rows = []
    page = 1
    while True:
        result = list_orders(hub_url, hub_key, page=page, per_page=100, **kwargs)
        if not result or 'error' in result or 'data' not in result:
            break
        rows.extend(result['data'])
        pg = result.get('pagination', {})
        if page >= pg.get('pages', 1):
            break
        page += 1
    return rows


def compute_today_kpis(hub_url: str, hub_key: str) -> dict:
    """
    Fetch all of today's orders (Tehran date) with full details and
    return a dict of KPI values for the dashboard and Telegram report.
    """
    from hub_client import get_order_detail

    today = _today_tehran()
    today_start = f"{today}T00:00:00"

    # --- Fetch all today's orders (by creation date, for sales/totals) ---
    all_summaries = _fetch_all_pages(hub_url, hub_key, date_after=today_start)

    # --- Fetch full detail for each order to get city/shipping/commission ---
    orders = []
    for summary in all_summaries:
        detail = get_order_detail(hub_url, hub_key, summary['id'])
        orders.append(detail if detail else summary)

    # --- Cancelled today: count by modification date so orders cancelled from
    #     previous days are included (creation-date fetch misses them) ---
    cancelled_today_ids: set = set()
    for _cstatus in ('cancelled', 'bslm-rejected'):
        for _o in _fetch_all_pages(hub_url, hub_key,
                                    status=_cstatus,
                                    date_modified_after=today_start):
            cancelled_today_ids.add(_o['id'])

    # --- Packages shipped today: separate queries by transition type ---
    # Case 1: processing → completed (exact: WooCommerce sets date_completed when status enters 'completed')
    completed_today = _fetch_all_pages(hub_url, hub_key, status='completed',
                                       date_completed_after=today_start)
    packages_shipped = len(completed_today)

    # Case 2: bslm-preparation → bslm-shipping (best-effort: date_modified is the transition proxy)
    bslm_shipped_today = _fetch_all_pages(hub_url, hub_key, status='bslm-shipping',
                                          date_modified_after=today_start)
    packages_shipped += len(bslm_shipped_today)

    # --- Compute KPIs ---
    kpis: dict = {
        'date': today,
        'hub_up': True,
        'total_orders': 0,
        'basalam_orders': 0,
        'website_orders': 0,
        'cancelled_orders': len(cancelled_today_ids),
        'free_ship_orders': 0,
        'daily_sales': 0.0,
        'daily_sales_net': 0.0,        # excl Basalam commission + shipping
        'total_shipping': 0.0,
        'sales_excl_shipping': 0.0,
        'basalam_total_sales': 0.0,
        'basalam_net_sales': 0.0,      # Basalam balance (after commission)
        'packages_shipped': packages_shipped,  # pre-computed via transition queries
        'qom_orders': 0,
        'non_qom_orders': 0,
        'commission_missing_count': 0,
        'notes': [],
    }

    for o in orders:
        status = o.get('status', '')
        src = o.get('order_source', '')
        total = float(o.get('total') or 0)
        shipping = float(o.get('shipping_total') or 0)
        meta = o.get('meta') or {}

        # Cancelled/rejected: skip from sales metrics (count already pre-computed above)
        if status in _CANCELLED_STATUSES:
            continue

        # Pending: skip entirely
        if status == 'pending':
            continue

        kpis['total_orders'] += 1

        # Source breakdown
        if src == 'basalam':
            kpis['basalam_orders'] += 1
        elif src == 'website':
            kpis['website_orders'] += 1

        # Free shipping: zero shipping AND order total above threshold
        if shipping == 0 and total >= _FREE_SHIP_THRESHOLD:
            kpis['free_ship_orders'] += 1

        # Sales aggregates
        kpis['daily_sales'] += total
        kpis['total_shipping'] += shipping
        kpis['sales_excl_shipping'] += total - shipping

        # Basalam commission / net revenue
        bslm_balance_raw = meta.get('_basalam_balance_amount')
        if src == 'basalam':
            kpis['basalam_total_sales'] += total
            if bslm_balance_raw is not None:
                bslm_balance = float(bslm_balance_raw)
                kpis['basalam_net_sales'] += bslm_balance
                kpis['daily_sales_net'] += bslm_balance
            else:
                kpis['commission_missing_count'] += 1
                fallback = total - shipping
                kpis['basalam_net_sales'] += fallback
                kpis['daily_sales_net'] += fallback
        else:
            kpis['daily_sales_net'] += total - shipping

        # Qom / non-Qom
        if _is_qom(o):
            kpis['qom_orders'] += 1
        else:
            kpis['non_qom_orders'] += 1

    if kpis['commission_missing_count']:
        kpis['notes'].append(
            f"{kpis['commission_missing_count']} سفارش باسلام فاقد اطلاعات کمیسیون — "
            "مبلغ خالص تخمینی (مجموع منهای هزینه ارسال)"
        )
    if len(bslm_shipped_today) > 0:
        kpis['notes'].append(
            f"بسته‌های باسلام ارسال شده ({len(bslm_shipped_today)} عدد): "
            "بر اساس آخرین تغییر سفارش — دقت تقریبی"
        )

    # Persian date
    try:
        import jdatetime, datetime as _dt
        gdate = _dt.date.fromisoformat(today)
        jd = jdatetime.date.fromgregorian(date=gdate)
        kpis['date_persian'] = f"{jd.year}/{jd.month:02d}/{jd.day:02d}"
    except Exception:
        kpis['date_persian'] = today

    return kpis


# ---------------------------------------------------------------------------
# Telegram report formatting
# ---------------------------------------------------------------------------

def format_telegram_report(kpis: dict) -> str:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import settings_manager as _sm
        cfg = _sm.load()
        template = cfg.get('templates', {}).get('daily_report', '')
    except Exception:
        template = ''

    if not template:
        # Fall back to default template from _DEFAULTS
        try:
            template = _sm._DEFAULTS['templates']['daily_report']
        except Exception:
            template = ''

    d = kpis.get('date_persian') or kpis.get('date', '')

    vars_dict = {
        'date':                 d,
        'total_orders':         _fmt_num(kpis.get('total_orders', 0)),
        'website_orders':       _fmt_num(kpis.get('website_orders', 0)),
        'basalam_orders':       _fmt_num(kpis.get('basalam_orders', 0)),
        'cancelled_orders':     _fmt_num(kpis.get('cancelled_orders', 0)),
        'free_ship_orders':     _fmt_num(kpis.get('free_ship_orders', 0)),
        'daily_sales':          _fmt_money(kpis.get('daily_sales', 0)),
        'sales_excl_shipping':  _fmt_money(kpis.get('sales_excl_shipping', 0)),
        'daily_sales_net':      _fmt_money(kpis.get('daily_sales_net', 0)),
        'total_shipping':       _fmt_money(kpis.get('total_shipping', 0)),
        'basalam_total_sales':  _fmt_money(kpis.get('basalam_total_sales', 0)),
        'basalam_net_sales':    _fmt_money(kpis.get('basalam_net_sales', 0)),
        'packages_shipped':     _fmt_num(kpis.get('packages_shipped', 0)),
        'qom_orders':           _fmt_num(kpis.get('qom_orders', 0)),
        'non_qom_orders':       _fmt_num(kpis.get('non_qom_orders', 0)),
    }

    try:
        text = template.format_map(vars_dict)
    except Exception:
        text = template

    if kpis.get('notes'):
        text += "\n\n⚠️ <b>یادداشت</b>\n" + "\n".join(f"• {n}" for n in kpis['notes'])

    text += "\n\n" + "─" * 28 + "\n⏰ ارسال خودکار ۲۳:۵۵ به وقت تهران"
    return text


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def _telegram_send_text(token: str, chat_id: str, text: str) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'HTML',
        }, timeout=15)
        data = r.json()
        if not data.get('ok'):
            _log.warning("Telegram sendMessage error: %s", data.get('description'))
        return bool(data.get('ok'))
    except Exception as exc:
        _log.error("Telegram sendMessage failed: %s", exc)
        return False


def send_daily_report(hub_url: str, hub_key: str, bot_token: str,
                      destinations_file: str) -> dict:
    """
    Compute today's KPIs and send the daily summary to all configured
    Telegram destinations.  Returns a result dict.
    """
    if not bot_token:
        return {'ok': False, 'error': 'TG_BOT_TOKEN not set'}

    import json, os
    destinations = []
    if os.path.exists(destinations_file):
        try:
            with open(destinations_file, 'r', encoding='utf-8') as f:
                destinations = json.load(f).get('destinations', [])
        except Exception:
            pass

    if not destinations:
        return {'ok': False, 'error': 'no destinations configured'}

    try:
        kpis = compute_today_kpis(hub_url, hub_key)
    except Exception as exc:
        _log.error("daily_report: KPI computation failed: %s", exc)
        return {'ok': False, 'error': str(exc)}

    text = format_telegram_report(kpis)
    sent, failed = 0, 0
    for dest in destinations:
        ok = _telegram_send_text(bot_token, str(dest['chat_id']), text)
        if ok:
            sent += 1
        else:
            failed += 1

    _log.info("daily_report: sent=%d failed=%d date=%s", sent, failed, kpis['date'])
    return {'ok': sent > 0, 'sent': sent, 'failed': failed, 'kpis': kpis}
