"""Thin wrapper around the Behdashtik Hub Data API."""

import re
import requests

_TIMEOUT = 8


def _get(url: str, api_key: str, params: dict = None) -> dict | None:
    try:
        r = requests.get(url, headers={'X-Hub-API-Key': api_key}, params=params or {}, timeout=_TIMEOUT)
        return r.json() if r.ok else {'error': r.text[:200]}
    except Exception as exc:
        return {'error': str(exc)}


def health(hub_url: str, api_key: str) -> dict:
    try:
        r = requests.get(
            f"{hub_url}/api/v1/health",
            headers={'X-Hub-API-Key': api_key},
            timeout=5,
        )
        body = r.json()
        return body.get('data', body)
    except Exception as exc:
        return {'error': str(exc)}


def orders_summary(hub_url: str, api_key: str, date_from: str = None, date_to: str = None) -> dict | None:
    params = {}
    if date_from:
        params['date_from'] = date_from
    if date_to:
        params['date_to'] = date_to
    result = _get(f"{hub_url}/api/v1/analytics/orders-summary", api_key, params)
    return result.get('data') if result and 'data' in result else result


def list_orders(hub_url: str, api_key: str, page: int = 1, per_page: int = 20,
                status: str = None, order_source: str = None,
                date_after: str = None, date_before: str = None,
                date_completed_after: str = None,
                date_modified_after: str = None,
                search: str = None) -> dict | None:
    params = {'page': page, 'per_page': per_page}
    if status:
        params['status'] = status
    if order_source:
        params['order_source'] = order_source
    if date_after:
        params['date_after'] = date_after
    if date_before:
        params['date_before'] = date_before
    if date_completed_after:
        params['date_completed_after'] = date_completed_after
    if date_modified_after:
        params['date_modified_after'] = date_modified_after
    if search:
        params['search'] = search
    return _get(f"{hub_url}/api/v1/orders", api_key, params)


def get_order_detail(hub_url: str, api_key: str, order_id: int) -> dict | None:
    """Fetch full order detail including billing, shipping, line_items, and meta."""
    result = _get(f"{hub_url}/api/v1/orders/{order_id}", api_key)
    return result.get('data') if result and 'data' in result else None


def list_products(hub_url: str, api_key: str, page: int = 1, per_page: int = 50,
                  stock_status: str = None) -> dict | None:
    params = {'page': page, 'per_page': per_page}
    if stock_status:
        params['stock_status'] = stock_status
    return _get(f"{hub_url}/api/v1/products", api_key, params)


def sync_status(hub_url: str, api_key: str) -> dict | None:
    return _get(f"{hub_url}/api/v1/sync/status", api_key)


# ---------------------------------------------------------------------------
# Tracking extraction (pure data; no HTTP call)
# ---------------------------------------------------------------------------

_TRACKING_META_KEYS = [
    '_tracking_number',
    '_woo_shiment_tracking_number',
    '_aftership_tracking_number',
    '_tracking_code',
    '_order_tracking_number',
    'tracking_number',
    '_yith_shipment_tracking_number',
    'post_barcode',          # Iran Post barcode (used on this store)
]

_ORDER_STATUS_LABELS = {
    'pending':          'در انتظار پرداخت',
    'processing':       'در حال پردازش',
    'on-hold':          'در انتظار',
    'completed':        'تکمیل‌شده',
    'cancelled':        'لغو شده',
    'refunded':         'مسترد شده',
    'failed':           'ناموفق',
    'ready-to-ship':    'آماده ارسال',
    'bslm-preparation': 'باسلام — آماده‌سازی',
    'bslm-shipping':    'باسلام — ارسال شده',
    'bslm-completed':   'باسلام — تکمیل‌شده',
    'bslm-rejected':    'باسلام — لغو شده',
    'bslm-wait-vendor': 'باسلام — انتظار فروشنده',
}


def _extract_from_shipment_items(val: str) -> str | None:
    """Parse tracking number from PHP-serialized _wc_shipment_tracking_items value."""
    if not val:
        return None
    m = re.search(r'"tracking_number";s:\d+:"([^"]+)"', val)
    return m.group(1) if m else None


def extract_tracking(order: dict) -> dict:
    """Extract postal tracking info from a full order dict returned by the Hub API."""
    order_id = order.get('id')
    status = order.get('status', '')
    meta = order.get('meta') or {}

    tracking_code: str | None = None
    for key in _TRACKING_META_KEYS:
        val = meta.get(key)
        if val and str(val).strip():
            tracking_code = str(val).strip()
            break
    # Fallback: PHP-serialized shipment tracking items
    if not tracking_code:
        raw = meta.get('_wc_shipment_tracking_items', '')
        if raw:
            tracking_code = _extract_from_shipment_items(str(raw))

    status_label = _ORDER_STATUS_LABELS.get(status, status)

    if status in ('pending', 'failed'):
        return {
            'order_id': order_id, 'order_status': status,
            'status': 'unpaid', 'status_label': 'پرداخت نشده',
            'tracking_code': None,
            'message': 'سفارش هنوز پرداخت نشده است.',
        }

    if status in ('cancelled', 'refunded', 'bslm-rejected'):
        return {
            'order_id': order_id, 'order_status': status,
            'status': 'cancelled', 'status_label': status_label,
            'tracking_code': None,
            'message': 'سفارش لغو یا مسترد شده است.',
        }

    if tracking_code:
        return {
            'order_id': order_id, 'order_status': status,
            'status': 'shipped', 'status_label': status_label or 'ارسال شده',
            'tracking_code': tracking_code,
            'message': f'کد رهگیری: {tracking_code}',
        }

    # Completed/shipped statuses without a tracking code: still mark as done, not in-progress
    if status in ('completed', 'bslm-completed', 'bslm-shipping'):
        return {
            'order_id': order_id, 'order_status': status,
            'status': 'shipped', 'status_label': status_label,
            'tracking_code': None,
            'message': 'سفارش ارسال و تکمیل شده است.',
        }

    return {
        'order_id': order_id, 'order_status': status,
        'status': 'in_progress', 'status_label': status_label,
        'tracking_code': None,
        'message': 'سفارش در حال پردازش است و هنوز ارسال نشده.',
    }
