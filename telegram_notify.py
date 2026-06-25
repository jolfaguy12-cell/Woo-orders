"""
Telegram notification module.

Notification sending: call send_order_notification(order, pdf_path)

Bot admin management (admin-only, chat ID 213946880):
  python telegram_notify.py bot

Admin commands:
  /menu                              show interactive menu
  /add <chat_id> <name> <type>      add destination  (type: user|group|channel)
  /remove <chat_id>                  remove destination
  /list                              list destinations
  /status                            show current config
"""

import json
import logging
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
_log = logging.getLogger(__name__)

# Dry-run mode: set TELEGRAM_DRY_RUN=1 to skip real API calls (used in tests)
_DRY_RUN: bool = bool(os.getenv('TELEGRAM_DRY_RUN'))
_dry_run_deleted: list = []    # (chat_id, message_id) pairs recorded when dry-run deletes
_dry_run_msg_counter: int = 0  # increments each dry-run send to produce unique fake IDs

ADMIN_CHAT_ID: int = int(os.getenv('TG_ADMIN_ID', '213946880'))
DESTINATIONS_FILE: str = os.getenv('TG_DESTINATIONS_FILE', 'telegram_destinations.json')
_TOKEN: str = os.getenv('TG_BOT_TOKEN', '')


# ---------------------------------------------------------------------------
# Telegram API helper
# ---------------------------------------------------------------------------

def _api(method: str, **kwargs) -> dict | None:
    url = f"https://api.telegram.org/bot{_TOKEN}/{method}"
    try:
        r = requests.post(url, timeout=15, **kwargs)
        data = r.json()
        if not data.get('ok'):
            _log.warning("Telegram [%s] error: %s", method, data.get('description'))
        return data
    except Exception as exc:
        _log.error("Telegram [%s] request failed: %s", method, exc)
        return None


# ---------------------------------------------------------------------------
# Destination management
# ---------------------------------------------------------------------------

def load_destinations() -> list[dict]:
    if os.path.exists(DESTINATIONS_FILE):
        try:
            with open(DESTINATIONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f).get('destinations', [])
        except Exception:
            pass
    return []


def _save_destinations(destinations: list[dict]):
    with open(DESTINATIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump({'destinations': destinations}, f, ensure_ascii=False, indent=2)


def add_destination(chat_id: str, name: str, dest_type: str) -> bool:
    """Add a destination. Returns False if chat_id already exists."""
    dests = load_destinations()
    if any(str(d['chat_id']) == str(chat_id) for d in dests):
        return False
    dests.append({'chat_id': str(chat_id), 'name': name, 'type': dest_type})
    _save_destinations(dests)
    return True


def remove_destination(chat_id: str) -> bool:
    """Remove a destination by chat_id. Returns False if not found."""
    dests = load_destinations()
    updated = [d for d in dests if str(d['chat_id']) != str(chat_id)]
    if len(updated) == len(dests):
        return False
    _save_destinations(updated)
    return True


# ---------------------------------------------------------------------------
# Notification sending
# ---------------------------------------------------------------------------

def _fmt(value) -> str:
    try:
        return "{:,.0f}".format(float(value))
    except (ValueError, TypeError):
        return str(value)


def _build_caption(order: dict) -> str:
    order_id = order.get('id', '?')
    status = order.get('status', '?')
    billing = order.get('billing', {})
    customer = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip() or '?'
    phone = billing.get('phone', '')
    total = _fmt(order.get('total', '0'))
    payment = order.get('payment_method_title', 'نامشخص')

    shipping = order.get('shipping', {})
    has_shipping = bool(shipping.get('first_name') or shipping.get('address_1'))
    shipping_warn = "🚨 نیاز به ارسال دارد\n" if has_shipping else ""
    if has_shipping:
        parts = [shipping.get('address_1', ''), shipping.get('address_2', ''), shipping.get('postcode', '')]
        addr = ' '.join(p for p in parts if p)
        shipping_info = f"📍 آدرس: {addr}\n"
    else:
        shipping_info = ""

    items = "\n".join(
        f"  ▪️ {i.get('name')} (x{i.get('quantity')})"
        for i in order.get('line_items', [])
    ) or "  —"

    return (
        f"📦 سفارش #{order_id}\n"
        f"📌 وضعیت: {status}\n"
        f"👤 مشتری: {customer}" + (f" | 📞 {phone}" if phone else "") + "\n"
        f"💳 پرداخت: {payment}\n"
        f"💰 مبلغ کل: {total} تومان\n"
        f"{shipping_warn}"
        f"{shipping_info}"
        f"\n🛒 محصولات:\n{items}"
    )


def _send_document(chat_id: str, caption: str, pdf_path: str) -> int | None:
    if _DRY_RUN:
        global _dry_run_msg_counter
        _dry_run_msg_counter += 1
        fake_id = 10000 + _dry_run_msg_counter
        _log.info("DRY RUN: would send document to %s → fake message_id=%d", chat_id, fake_id)
        return fake_id
    with open(pdf_path, 'rb') as f:
        result = _api(
            'sendDocument',
            data={'chat_id': chat_id, 'caption': caption},
            files={'document': f},
        )
    if result and result.get('ok'):
        return result['result']['message_id']
    return None


def _delete_message(chat_id: str, message_id: int):
    if _DRY_RUN:
        _dry_run_deleted.append((chat_id, message_id))
        _log.info("DRY RUN: would delete message %s from %s", message_id, chat_id)
        return
    result = _api('deleteMessage', json={'chat_id': chat_id, 'message_id': message_id})
    if not (result and result.get('ok')):
        _log.warning("Could not delete message %s in chat %s (may be >48h old)", message_id, chat_id)


def send_order_notification(order: dict, pdf_path: str):
    """
    Send (or update) Telegram notifications for an order.
    For each destination:
      - Deletes the previous bot message if one was stored.
      - Sends the new PDF with caption.
      - Stores the new message_id for future updates.
    """
    if not _DRY_RUN and not _TOKEN:
        _log.warning("TG_BOT_TOKEN not set; skipping Telegram notification.")
        return

    from order_state import get_message_id, set_message_id

    destinations = load_destinations()
    if not destinations:
        _log.warning("No Telegram destinations configured. Run 'python telegram_notify.py bot' to add some.")
        return

    caption = _build_caption(order)
    order_id = str(order.get('id', ''))

    for dest in destinations:
        chat_id = str(dest['chat_id'])

        prev_id = get_message_id(order_id, chat_id)
        if prev_id:
            _delete_message(chat_id, prev_id)

        msg_id = _send_document(chat_id, caption, pdf_path)
        if msg_id:
            set_message_id(order_id, chat_id, msg_id)
            _log.info("Notified %s (%s) for order %s", dest['name'], chat_id, order_id)
        else:
            _log.error("Failed to notify %s (%s) for order %s", dest['name'], chat_id, order_id)


# ---------------------------------------------------------------------------
# Bot: admin destination management
# ---------------------------------------------------------------------------

def _send_msg(chat_id: int | str, text: str, reply_markup: dict = None):
    payload: dict = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    if reply_markup:
        payload['reply_markup'] = reply_markup
    _api('sendMessage', json=payload)


def _main_menu() -> dict:
    return {
        'inline_keyboard': [
            [
                {'text': '📋 مقصدها', 'callback_data': 'menu:list'},
                {'text': '📊 وضعیت / تنظیمات', 'callback_data': 'menu:status'},
            ],
            [
                {'text': '➕ افزودن مقصد', 'callback_data': 'menu:help_add'},
                {'text': '🗑 حذف مقصد', 'callback_data': 'menu:help_remove'},
            ],
        ]
    }


def _back_kb() -> dict:
    return {'inline_keyboard': [[{'text': '↩️ بازگشت به منو', 'callback_data': 'menu:back'}]]}


def _cmd_list(chat_id: int):
    dests = load_destinations()
    if not dests:
        _send_msg(chat_id, "هیچ مقصدی تنظیم نشده است.", reply_markup=_back_kb())
        return
    lines = ["<b>مقصدهای فعال:</b>"]
    for d in dests:
        lines.append(f"• <b>{d['name']}</b> | {d['type']} | <code>{d['chat_id']}</code>")
    _send_msg(chat_id, "\n".join(lines), reply_markup=_back_kb())


def _cmd_status(chat_id: int):
    dests = load_destinations()
    target = os.getenv('TARGET_ORDER_STATUSES', 'processing,wc-ready-to-ship')
    bkeys = os.getenv('BASALAM_META_KEYS', '_order_source,source,channel,known_source,_basalam_order_id')
    _send_msg(chat_id, "\n".join([
        "<b>وضعیت پیکربندی</b>",
        f"مقصدها: {len(dests)} عدد",
        f"وضعیت‌های هدف: <code>{target}</code>",
        f"کلیدهای بصالام: <code>{bkeys}</code>",
        f"پایگاه داده: <code>{os.getenv('ORDER_STATE_DB', './data/order_state.sqlite3')}</code>",
        f"فایل مقصدها: <code>{DESTINATIONS_FILE}</code>",
    ]), reply_markup=_back_kb())


def _handle_command(msg: dict):
    chat_id: int = msg['chat']['id']
    if chat_id != ADMIN_CHAT_ID:
        _send_msg(chat_id, "⛔ دسترسی غیرمجاز.")
        return

    parts = msg.get('text', '').strip().split(maxsplit=3)
    cmd = parts[0].lstrip('/').lower()

    if cmd in ('start', 'menu'):
        _send_msg(chat_id, "<b>منوی مدیریت مقصدهای تلگرام</b>", reply_markup=_main_menu())

    elif cmd == 'add':
        if len(parts) < 4:
            _send_msg(chat_id, "استفاده: /add &lt;chat_id&gt; &lt;name&gt; &lt;type&gt;\nمثال: /add -1001234567890 گروه مدیران group")
            return
        _, cid, name, dtype = parts
        if dtype not in ('user', 'group', 'channel'):
            _send_msg(chat_id, "نوع باید یکی از: user, group, channel باشد.")
            return
        if add_destination(cid, name, dtype):
            _send_msg(chat_id, f"✅ مقصد «{name}» ({cid}) اضافه شد.")
        else:
            _send_msg(chat_id, f"⚠️ این chat_id قبلاً اضافه شده است.")

    elif cmd == 'remove':
        if len(parts) < 2:
            _send_msg(chat_id, "استفاده: /remove &lt;chat_id&gt;")
            return
        if remove_destination(parts[1]):
            _send_msg(chat_id, f"✅ مقصد {parts[1]} حذف شد.")
        else:
            _send_msg(chat_id, f"⚠️ مقصدی با chat_id {parts[1]} یافت نشد.")

    elif cmd == 'list':
        _cmd_list(chat_id)

    elif cmd == 'status':
        _cmd_status(chat_id)

    else:
        _send_msg(chat_id,
            "دستورات موجود:\n"
            "/menu — منوی اصلی\n"
            "/add &lt;chat_id&gt; &lt;name&gt; &lt;type&gt; — افزودن مقصد\n"
            "/remove &lt;chat_id&gt; — حذف مقصد\n"
            "/list — لیست مقصدها\n"
            "/status — وضعیت کلی"
        )


def _handle_callback(cb: dict):
    chat_id: int = cb['from']['id']
    _api('answerCallbackQuery', json={'callback_query_id': cb['id']})

    if chat_id != ADMIN_CHAT_ID:
        return

    data = cb.get('data', '')
    if data == 'menu:back':
        _send_msg(chat_id, "<b>منوی مدیریت مقصدهای تلگرام</b>", reply_markup=_main_menu())
    elif data == 'menu:list':
        _cmd_list(chat_id)
    elif data == 'menu:status':
        _cmd_status(chat_id)
    elif data == 'menu:help_add':
        _send_msg(chat_id,
            "برای افزودن مقصد:\n"
            "<code>/add &lt;chat_id&gt; &lt;name&gt; &lt;type&gt;</code>\n\n"
            "مثال:\n<code>/add -1001234567890 گروه مدیران group</code>\n\n"
            "نوع (type): user | group | channel",
            reply_markup=_back_kb()
        )
    elif data == 'menu:help_remove':
        _send_msg(chat_id,
            "برای حذف مقصد:\n"
            "<code>/remove &lt;chat_id&gt;</code>\n\n"
            "مثال:\n<code>/remove -1001234567890</code>",
            reply_markup=_back_kb()
        )


def run_bot():
    """Long-polling bot loop for admin destination management."""
    if not _TOKEN:
        print("Error: TG_BOT_TOKEN is not set in .env")
        sys.exit(1)

    print(f"Bot started. Listening for admin (ID: {ADMIN_CHAT_ID})...")
    offset: int | None = None

    while True:
        try:
            params: dict = {'timeout': 30, 'allowed_updates': ['message', 'callback_query']}
            if offset is not None:
                params['offset'] = offset

            result = _api('getUpdates', json=params)
            if not result or not result.get('ok'):
                time.sleep(5)
                continue

            for update in result.get('result', []):
                offset = update['update_id'] + 1
                if 'message' in update and 'text' in update['message']:
                    _handle_command(update['message'])
                elif 'callback_query' in update:
                    _handle_callback(update['callback_query'])

        except KeyboardInterrupt:
            print("Bot stopped.")
            break
        except Exception as exc:
            _log.error("Bot polling error: %s", exc)
            time.sleep(5)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'bot':
        run_bot()
    else:
        print(__doc__)
