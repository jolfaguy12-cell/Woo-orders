#!/usr/bin/env /usr/bin/python3
"""
Webhook receiver for Hub order events.

POST /webhook/hub-order  — accepts order.upserted and order.deleted from the Hub.

Listens on WEBHOOK_PORT (default 5100, because port 5000 is reserved for the Hub).

Environment variables (set in .env):
  WEBHOOK_SECRET  — shared HMAC-SHA256 secret from the Hub endpoint configuration
  WEBHOOK_PORT    — port to listen on (default 5100)
"""

import hashlib
import hmac
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
_log = logging.getLogger(__name__)

_SECRET = os.getenv('WEBHOOK_SECRET', '')
_PORT   = int(os.getenv('WEBHOOK_PORT', '5100'))

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------

def _verify_sig(body: bytes, header_sig: str) -> bool:
    if not _SECRET:
        _log.warning("WEBHOOK_SECRET not configured — signature check skipped (insecure)")
        return True
    expected = hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_sig or '')


# ---------------------------------------------------------------------------
# Webhook endpoint
# ---------------------------------------------------------------------------

@app.post('/webhook/hub-order')
def hub_order():
    body      = request.get_data()
    signature = request.headers.get('X-BDSK-Signature', '')

    if not _verify_sig(body, signature):
        _log.warning("webhook rejected  reason=invalid_signature")
        return jsonify({'error': 'invalid_signature'}), 401

    try:
        payload = json.loads(body)
    except Exception:
        _log.warning("webhook rejected  reason=invalid_json")
        return jsonify({'error': 'invalid_json'}), 400

    event     = payload.get('event', '')
    entity_id = payload.get('entity_id')
    data      = payload.get('data') or {}
    order_id  = str(data.get('id') or entity_id or 'UNKNOWN')

    _log.info("webhook received  event=%s order_id=%s", event, order_id)

    if event == 'order.upserted':
        return _handle_upserted(order_id, data)
    if event == 'order.deleted':
        return _handle_deleted(order_id)

    _log.info("webhook skipped   event=%s order_id=%s  reason=unhandled_event", event, order_id)
    return jsonify({'result': 'skipped', 'reason': 'unhandled_event'}), 200


# ---------------------------------------------------------------------------
# order.upserted
# ---------------------------------------------------------------------------

def _handle_upserted(order_id: str, order: dict):
    try:
        from process_order import process_order
        result  = process_order(order)
        outcome = 'processed' if result.get('notified') else 'skipped'
        _log.info(
            "webhook %s     order_id=%s skip_reason=%s",
            outcome, order_id, result.get('skipped_reason'),
        )
        return jsonify({'result': outcome, 'order_id': order_id, 'detail': result}), 200
    except Exception as exc:
        _log.error("webhook failed    order_id=%s error=%s", order_id, exc)
        return jsonify({'result': 'failed', 'order_id': order_id, 'error': str(exc)}), 500


# ---------------------------------------------------------------------------
# order.deleted
# ---------------------------------------------------------------------------

def _handle_deleted(order_id: str):
    try:
        from order_state import init_db
        from telegram_notify import delete_order_messages

        init_db()
        n = delete_order_messages(order_id)
        _purge_state(order_id)
        _log.info("webhook deleted   order_id=%s telegram_msgs_removed=%d", order_id, n)
        return jsonify({'result': 'processed', 'order_id': order_id, 'deleted_messages': n}), 200
    except Exception as exc:
        _log.error("webhook failed    order_id=%s error=%s", order_id, exc)
        return jsonify({'result': 'failed', 'order_id': order_id, 'error': str(exc)}), 500


def _purge_state(order_id: str):
    import sqlite3
    db_path = os.getenv('ORDER_STATE_DB', './data/order_state.sqlite3')
    with sqlite3.connect(db_path) as conn:
        conn.execute("DELETE FROM message_ids WHERE order_id = ?", (str(order_id),))
        conn.execute("DELETE FROM order_state  WHERE order_id = ?", (str(order_id),))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if not _SECRET:
        _log.warning("WEBHOOK_SECRET is not set — configure it in .env before going to production")
    _log.info("Hub order webhook server starting on 0.0.0.0:%d", _PORT)
    app.run(host='0.0.0.0', port=_PORT, debug=False)
