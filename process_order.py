"""
Main entrypoint for processing a WooCommerce order.

process_order(order: dict) is the reusable function intended to be called
by an external webhook hub, a scheduler, or the CLI below.

CLI usage:
    python process_order.py [path/to/order.json]
    python process_order.py sample_order.json
"""

import json
import os
import sys
from dotenv import load_dotenv

load_dotenv()

from basalam_detect import is_basalam_order
from order_state import cleanup_old_records, get_order_state, init_db, set_order_state
from pdf_generator import generate_pdf
from telegram_notify import send_order_notification

TARGET_ORDER_STATUSES: list[str] = [
    s.strip()
    for s in os.getenv(
        'TARGET_ORDER_STATUSES',
        'processing,ready-to-ship,bslm-preparation,bslm-shipping,bslm-wait-vendor,bslm-rejected,refunded',
    ).split(',')
    if s.strip()
]


def _read_settings() -> dict:
    try:
        f = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'dashboard', 'data', 'settings.json')
        with open(f, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


def _should_send_pdf() -> bool:
    return bool(_read_settings().get('send_pdf_with_new_order', True))


def _is_basalam_only() -> bool:
    return bool(_read_settings().get('basalam_only', False))


def _normalize_status(status: str) -> str:
    """Strip the leading 'wc-' prefix that WordPress adds to custom status slugs.

    Hub normally does this before sending the webhook payload, but if a raw
    WooCommerce status arrives (e.g. 'wc-bslm-preparation') this ensures the
    comparison against TARGET_ORDER_STATUSES still works.
    """
    return status[3:] if status.startswith('wc-') else status


def process_order(order: dict) -> dict:
    """
    Process a single WooCommerce order dict:
      1. Check Basalam origin.
      2. Check status against TARGET_ORDER_STATUSES.
      3. Generate PDF invoice + packing slip.
      4. Send/update Telegram notification.
      5. Persist state for future updates.

    Returns a result dict with keys:
        order_id, status, pdf, notified, skipped_reason
    """
    init_db()
    cleanup_old_records()

    order_id = str(order.get('id', 'UNKNOWN'))
    status = order.get('status', '')
    status_key = _normalize_status(status)

    result: dict = {
        'order_id': order_id,
        'status': status,
        'pdf': None,
        'notified': False,
        'skipped_reason': None,
    }

    if not is_basalam_order(order) and _is_basalam_only():
        result['skipped_reason'] = 'basalam_only_mode'
        print(f"Order {order_id}: basalam_only=true — skipping non-Basalam order.")
        return result

    if status_key not in TARGET_ORDER_STATUSES:
        result['skipped_reason'] = f'status_not_targeted ({status!r})'
        print(f"Order {order_id}: status {status!r} not in target list — skipping.")
        return result

    prev = get_order_state(order_id)
    if prev and _normalize_status(prev['status']) == status_key and prev['notified']:
        result['skipped_reason'] = 'already_notified_for_this_status'
        print(f"Order {order_id}: already notified for status {status!r} — skipping.")
        return result

    if _should_send_pdf():
        pdf_path = generate_pdf(order)
    else:
        pdf_path = None
    result['pdf'] = pdf_path

    send_order_notification(order, pdf_path)
    result['notified'] = True

    set_order_state(order_id, status, notified=True)
    return result


if __name__ == '__main__':
    json_path = sys.argv[1] if len(sys.argv) > 1 else 'sample_order.json'
    print(f"Processing: {json_path}\n")
    with open(json_path, 'r', encoding='utf-8') as f:
        order = json.load(f)
    result = process_order(order)
    print(f"\nResult:\n{json.dumps(result, ensure_ascii=False, indent=2)}")
