#!/usr/bin/env python3
"""
Dry-run flow validation — no real Telegram messages, no WeasyPrint/fonts needed.

Exercises all 4 order scenarios against a temporary SQLite state DB.

Usage:
    python test_flow.py
    python test_flow.py --verbose    # show detail on passing checks too
"""

import os
import sys
import json
import shutil
import tempfile
import unittest.mock as mock

# ---------------------------------------------------------------------------
# Set ALL env vars before importing any project module.
# Module-level code in order_state, telegram_notify, process_order reads env
# at import time, so env must be ready first.
# ---------------------------------------------------------------------------
_TEST_DIR = tempfile.mkdtemp(prefix='woo_test_')
_DB_FILE = os.path.join(_TEST_DIR, 'test_state.sqlite3')
_DEST_FILE = os.path.join(_TEST_DIR, 'test_destinations.json')

os.environ.update({
    'TELEGRAM_DRY_RUN': '1',
    'ORDER_STATE_DB': _DB_FILE,
    'TG_DESTINATIONS_FILE': _DEST_FILE,
    'TARGET_ORDER_STATUSES': 'processing,wc-ready-to-ship',
    'STATE_RETENTION_DAYS': '30',
    # TG_BOT_TOKEN is intentionally NOT overridden here so the real token from
    # .env is loaded by telegram_notify.load_dotenv(). Dry-run mode still
    # prevents any real API calls, but the token path is exercised.
})

# ---------------------------------------------------------------------------
# Mock pdf_generator at the module level so WeasyPrint is never imported.
# Must happen before process_order is imported (it does `from pdf_generator import ...`).
# ---------------------------------------------------------------------------
_FAKE_PDF = os.path.join(_TEST_DIR, 'fake_order.pdf')
open(_FAKE_PDF, 'wb').close()  # empty file; dry-run _send_document never opens it

_mock_pdf_mod = mock.MagicMock()
_mock_pdf_mod.generate_pdf.return_value = _FAKE_PDF
sys.modules['pdf_generator'] = _mock_pdf_mod

# Also block weasyprint if it happens to be imported transitively
sys.modules.setdefault('weasyprint', mock.MagicMock())

# ---------------------------------------------------------------------------
# Now import project modules
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import telegram_notify           # noqa: E402
import order_state               # noqa: E402
from process_order import process_order   # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
_DEST_CHAT_ID = '-999000'
_ORDER_ID = '12345'
_VERBOSE = '--verbose' in sys.argv


def _setup():
    order_state.init_db()
    with open(_DEST_FILE, 'w', encoding='utf-8') as f:
        json.dump({'destinations': [
            {'chat_id': _DEST_CHAT_ID, 'name': 'Test Dest', 'type': 'group'}
        ]}, f)


def _sample(overrides: dict) -> dict:
    base = {
        'id': 12345,
        'status': 'processing',
        'date_created': '2023-10-25T14:30:00',
        'created_via': 'checkout',
        'total': '530000',
        'shipping_total': '30000',
        'total_tax': '0',
        'discount_total': '0',
        'customer_note': '',
        'payment_method_title': 'پرداخت آنلاین',
        'billing': {'first_name': 'علی', 'last_name': 'رضایی', 'phone': '09123456789'},
        'shipping': {'first_name': 'علی', 'last_name': 'رضایی',
                     'address_1': 'تهران، خیابان آزادی', 'address_2': '', 'postcode': '1234567890'},
        'line_items': [{'name': 'محصول', 'quantity': 1, 'price': '530000', 'total': '530000'}],
        'shipping_lines': [{'method_title': 'پست'}],
        'meta_data': [{'key': 'source', 'value': 'basalam'}],
    }
    return {**base, **overrides}


# ---------------------------------------------------------------------------
# Check functions — each returns (passed: bool, detail: str)
# ---------------------------------------------------------------------------

def check1_basalam_processing():
    """Basalam + processing → notify + store state and message_id."""
    order = _sample({'id': 12345, 'status': 'processing'})
    result = process_order(order)

    state = order_state.get_order_state(_ORDER_ID)
    mid = order_state.get_message_id(_ORDER_ID, _DEST_CHAT_ID)

    ok = (
        result['notified'] is True
        and result['skipped_reason'] is None
        and state is not None
        and state['notified'] == 1
        and mid is not None
    )
    return ok, f"notified={result['notified']} reason={result['skipped_reason']} msg_id={mid}"


def check2_status_change():
    """Same order, new target status → delete old msg_id, send new, update stored id."""
    telegram_notify._dry_run_deleted.clear()
    mid_before = order_state.get_message_id(_ORDER_ID, _DEST_CHAT_ID)

    order = _sample({'id': 12345, 'status': 'wc-ready-to-ship'})
    result = process_order(order)

    mid_after = order_state.get_message_id(_ORDER_ID, _DEST_CHAT_ID)
    deleted_old = any(mid == mid_before for (_, mid) in telegram_notify._dry_run_deleted)

    ok = (
        result['notified'] is True
        and mid_after is not None
        and mid_after != mid_before
        and deleted_old
    )
    return ok, (
        f"notified={result['notified']} "
        f"mid_before={mid_before} mid_after={mid_after} "
        f"deleted_old={deleted_old} deleted_list={telegram_notify._dry_run_deleted}"
    )


def check3_non_target_status():
    """Basalam + non-target status (completed) → skip, no notification."""
    order = _sample({'id': 99991, 'status': 'completed'})
    result = process_order(order)

    ok = (
        result['notified'] is False
        and 'status_not_targeted' in (result['skipped_reason'] or '')
    )
    return ok, f"notified={result['notified']} reason={result['skipped_reason']}"


def check4_non_basalam():
    """Non-Basalam order + processing → skip, no notification."""
    order = _sample({'id': 99992, 'status': 'processing', 'meta_data': []})
    result = process_order(order)

    ok = (
        result['notified'] is False
        and result['skipped_reason'] == 'not_basalam'
    )
    return ok, f"notified={result['notified']} reason={result['skipped_reason']}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    _setup()

    checks = [
        ("Basalam + processing → notified + state + msg_id stored", check1_basalam_processing),
        ("Status change → old msg deleted, new msg_id stored", check2_status_change),
        ("Basalam + non-target status → skipped (no notification)", check3_non_target_status),
        ("Non-Basalam + processing → skipped (not_basalam)", check4_non_basalam),
    ]

    all_passed = True
    for label, fn in checks:
        try:
            ok, detail = fn()
        except Exception as exc:
            ok, detail = False, f"EXCEPTION: {exc}"
        tag = 'PASS' if ok else 'FAIL'
        if not ok:
            all_passed = False
        print(f"[{tag}] {label}")
        if not ok or _VERBOSE:
            print(f"       {detail}")

    print()
    print('All checks passed.' if all_passed else 'SOME CHECKS FAILED.')
    return 0 if all_passed else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(_TEST_DIR, ignore_errors=True)
