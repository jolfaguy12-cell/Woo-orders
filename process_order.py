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
    for s in os.getenv('TARGET_ORDER_STATUSES', 'processing,wc-ready-to-ship').split(',')
    if s.strip()
]


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

    result: dict = {
        'order_id': order_id,
        'status': status,
        'pdf': None,
        'notified': False,
        'skipped_reason': None,
    }

    if not is_basalam_order(order):
        result['skipped_reason'] = 'not_basalam'
        print(f"Order {order_id}: not a Basalam order — skipping.")
        return result

    if status not in TARGET_ORDER_STATUSES:
        result['skipped_reason'] = f'status_not_targeted ({status!r})'
        print(f"Order {order_id}: status {status!r} not in target list — skipping.")
        return result

    prev = get_order_state(order_id)
    if prev and prev['status'] == status and prev['notified']:
        result['skipped_reason'] = 'already_notified_for_this_status'
        print(f"Order {order_id}: already notified for status {status!r} — skipping.")
        return result

    pdf_path = generate_pdf(order)
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
