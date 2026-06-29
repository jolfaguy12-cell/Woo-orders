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


_STATUS_LABELS: dict[str, str] = {
    "processing":        "در حال پردازش",
    "pending":           "در انتظار پرداخت",
    "completed":         "تکمیل‌شده",
    "cancelled":         "لغو شده",
    "refunded":          "مسترد شده",
    "failed":            "ناموفق",
    "on-hold":           "در انتظار",
    "ready-to-ship":     "آماده ارسال",
    # Basalam-specific statuses — stored with wc- prefix in DB, stripped by Hub.
    # Both forms accepted so the app is robust to either format arriving.
    "bslm-preparation":     "باسلام — آماده‌سازی سفارش",
    "wc-bslm-preparation":  "باسلام — آماده‌سازی سفارش",
    "bslm-shipping":        "باسلام — سفارش برای مشتری",
    "wc-bslm-shipping":     "باسلام — سفارش برای مشتری",
    "bslm-completed":       "باسلام — تکمیل‌شده",
    "wc-bslm-completed":    "باسلام — تکمیل‌شده",
    "bslm-rejected":        "باسلام — لغو شده",
    "wc-bslm-rejected":     "باسلام — لغو شده",
    "bslm-wait-vendor":     "باسلام — انتظار فروشنده",
    "wc-bslm-wait-vendor":  "باسلام — انتظار فروشنده",
}


def _build_caption(order: dict) -> str:
    order_id = order.get('id', '?')
    status_raw = order.get('status', '?')
    status_label = _STATUS_LABELS.get(status_raw, status_raw)
    billing = order.get('billing', {})
    customer = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip() or '?'
    phone = billing.get('phone', '')
    total = _fmt(order.get('total', '0'))
    payment = order.get('payment_method_title', 'نامشخص')
    is_basalam = bool(order.get('basalam'))

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

    source_badge = "🛍 باسلام\n" if is_basalam else ""

    caption = (
        f"📦 سفارش #{order_id}\n"
        f"{source_badge}"
        f"📌 وضعیت: {status_label}\n"
        f"👤 مشتری: {customer}" + (f" | 📞 {phone}" if phone else "") + "\n"
        f"💳 پرداخت: {payment}\n"
        f"💰 مبلغ کل: {total} تومان\n"
        f"{shipping_warn}"
        f"{shipping_info}"
        f"\n🛒 محصولات:\n{items}"
    )

    # Append Basalam financial breakdown (commission, payable amount, customer stats)
    if is_basalam:
        bs = order['basalam']
        fee    = _fmt(bs.get('fee_amount', '0'))
        net    = _fmt(bs.get('balance_amount', '0'))
        pcount = bs.get('purchase_count', 0)
        caption += (
            f"\n\n📊 اطلاعات باسلام:\n"
            f"  کارمزد: {fee} تومان\n"
            f"  مبلغ قابل دریافت: {net} تومان\n"
            f"  تعداد خرید مشتری: {pcount}"
        )

    return caption


def _send_text(chat_id: str, text: str, parse_mode: str = None) -> int | None:
    if _DRY_RUN:
        global _dry_run_msg_counter
        _dry_run_msg_counter += 1
        fake_id = 10000 + _dry_run_msg_counter
        _log.info("DRY RUN: would send text to %s → fake message_id=%d", chat_id, fake_id)
        return fake_id
    payload: dict = {'chat_id': chat_id, 'text': text}
    if parse_mode:
        payload['parse_mode'] = parse_mode
    result = _api('sendMessage', json=payload)
    if result and result.get('ok'):
        return result['result']['message_id']
    return None


def _send_document(chat_id: str, caption: str, pdf_path: str, parse_mode: str = None) -> int | None:
    if _DRY_RUN:
        global _dry_run_msg_counter
        _dry_run_msg_counter += 1
        fake_id = 10000 + _dry_run_msg_counter
        _log.info("DRY RUN: would send document to %s → fake message_id=%d", chat_id, fake_id)
        return fake_id
    data: dict = {'chat_id': chat_id, 'caption': caption[:1024]}
    if parse_mode:
        data['parse_mode'] = parse_mode
    with open(pdf_path, 'rb') as f:
        result = _api('sendDocument', data=data, files={'document': f})
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


def delete_order_messages(order_id: str) -> int:
    """Delete all previously sent Telegram messages for an order. Returns count deleted."""
    from order_state import get_message_id
    destinations = load_destinations()
    count = 0
    for dest in destinations:
        chat_id = str(dest['chat_id'])
        msg_id  = get_message_id(str(order_id), chat_id)
        if msg_id:
            _delete_message(chat_id, msg_id)
            count += 1
    return count


def send_order_notification(order: dict, pdf_path: str | None):
    """
    Send (or update) Telegram notifications for an order.
    Uses the dashboard 'new_order'/'basalam_order' template from settings.json.
    Recipients: notification destinations + manager IDs from the whitelist.
    """
    if not _DRY_RUN and not _TOKEN:
        _log.warning("TG_BOT_TOKEN not set; skipping Telegram notification.")
        return

    from order_state import get_message_id, set_message_id

    text = _render_order_message(order)
    order_id = str(order.get('id', ''))

    # --- notification destinations (with message-ID tracking for updates) ---
    destinations = load_destinations()
    dest_chat_ids: set[str] = set()
    for dest in destinations:
        chat_id = str(dest['chat_id'])
        dest_chat_ids.add(chat_id)
        prev_id = get_message_id(order_id, chat_id)
        if prev_id:
            _delete_message(chat_id, prev_id)
        if pdf_path:
            msg_id = _send_document(chat_id, text, pdf_path)
        else:
            msg_id = _send_text(chat_id, text)
        if msg_id:
            set_message_id(order_id, chat_id, msg_id)
            _log.info("Notified %s (%s) for order %s", dest['name'], chat_id, order_id)
        else:
            _log.error("Failed to notify %s (%s) for order %s", dest['name'], chat_id, order_id)

    if not destinations:
        _log.info("No notification destinations configured for order %s.", order_id)

    # --- manager IDs from dashboard whitelist (no message-ID tracking) ---
    try:
        cfg = _load_settings_json()
        manager_ids = [int(x) for x in cfg.get('telegram_manager_ids', []) if str(x).strip()]
    except Exception:
        manager_ids = []

    for mgr_id in manager_ids:
        if str(mgr_id) in dest_chat_ids:
            continue  # already sent via destinations list
        if pdf_path:
            _send_document(str(mgr_id), text, pdf_path)
        else:
            _send_text(str(mgr_id), text)
        _log.info("Notified manager %s for order %s", mgr_id, order_id)


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
    target = os.getenv('TARGET_ORDER_STATUSES', 'processing,ready-to-ship,bslm-preparation,bslm-shipping,bslm-wait-vendor,bslm-rejected')
    _send_msg(chat_id, "\n".join([
        "<b>وضعیت پیکربندی</b>",
        f"مقصدها: {len(dests)} عدد",
        f"وضعیت‌های هدف: <code>{target}</code>",
        f"پایگاه داده: <code>{os.getenv('ORDER_STATE_DB', './data/order_state.sqlite3')}</code>",
        f"فایل مقصدها: <code>{DESTINATIONS_FILE}</code>",
    ]), reply_markup=_back_kb())


def _normalize_query(text: str) -> str:
    """Normalize Persian/Arabic-Indic digits to ASCII before sending to Hub search."""
    _PER = str.maketrans('۰۱۲۳۴۵۶۷۸۹', '0123456789')
    _ARA = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')
    return text.translate(_PER).translate(_ARA).strip()


def _load_settings_json() -> dict:
    """Read dashboard/data/settings.json directly. Returns {} on failure."""
    try:
        f = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'dashboard', 'data', 'settings.json')
        with open(f, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


class _FallbackDict(dict):
    """dict that returns '' for missing keys (safe format_map)."""
    def __missing__(self, key):
        return ''


def _render_order_message(order: dict) -> str:
    """Render new-order notification from dashboard template. Falls back to _build_caption()."""
    try:
        cfg = _load_settings_json()
        is_basalam = bool(order.get('basalam'))
        tpl_key = 'basalam_order' if is_basalam else 'new_order'
        tpl = (cfg.get('templates') or {}).get(tpl_key, '')
        if not tpl:
            return _build_caption(order)

        status_raw = order.get('status', '')
        billing = order.get('billing', {})
        customer_name = (
            f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip() or '—'
        )

        # Jalali date with ASCII fallback
        try:
            import jdatetime as _jd
            from datetime import datetime as _dtc
            _d = _dtc.strptime((order.get('date_created') or '')[:10], '%Y-%m-%d')
            order_date = _jd.date.fromgregorian(date=_d.date()).strftime('%Y/%m/%d')
        except Exception:
            order_date = (order.get('date_created') or '')[:10] or '—'

        items_list = '\n'.join(
            f"  ▪ {i.get('name', '—')} × {i.get('quantity', 1)}"
            for i in order.get('line_items', [])
        ) or '  —'

        vars_: dict = _FallbackDict({
            'order_id':       order.get('id', '?'),
            'status':         status_raw,
            'status_label':   _STATUS_LABELS.get(status_raw, status_raw),
            'customer_name':  customer_name,
            'phone':          billing.get('phone', '') or '—',
            'email':          billing.get('email', '') or '—',
            'total':          _fmt(order.get('total', '0')),
            'payment_method': order.get('payment_method_title', 'نامشخص'),
            'order_date':     order_date,
            'items_list':     items_list,
        })
        if is_basalam:
            bs = order.get('basalam', {})
            vars_['basalam_fee'] = _fmt(bs.get('fee_amount', '0'))
            vars_['basalam_net'] = _fmt(bs.get('balance_amount', '0'))
            vars_['basalam_purchase_count'] = str(bs.get('purchase_count', 0))

        return tpl.format_map(vars_)
    except Exception:
        return _build_caption(order)


def _send_invoice_for_admin(chat_id: int, order: dict, order_id: int, caption: str = '') -> bool:
    """Send PDF invoice to admin with optional caption (Telegram caption limit: 1024 chars).
    Returns True if the PDF was sent, False if unavailable (caller should fall back to text)."""
    import glob as _glob
    project_root = os.path.dirname(os.path.abspath(__file__))
    output_root = os.path.join(project_root, 'output')

    matches = _glob.glob(os.path.join(output_root, '**', f'*_{order_id}.pdf'), recursive=True)
    if matches:
        pdf_path = max(matches, key=os.path.getmtime)
    else:
        try:
            from pdf_generator import generate_pdf as _gen_pdf
            pdf_path = _gen_pdf(order)
        except Exception as exc:
            _log.warning("PDF generation for order %s failed: %s", order_id, exc)
            return False

    if _DRY_RUN:
        _log.info("DRY RUN: would send invoice PDF %s to admin %s", pdf_path, chat_id)
        return True
    try:
        with open(pdf_path, 'rb') as f:
            data: dict = {'chat_id': str(chat_id)}
            if caption:
                data['caption'] = caption[:1024]
                data['parse_mode'] = 'HTML'
            _api('sendDocument',
                 data=data,
                 files={'document': (f'invoice_{order_id}.pdf', f, 'application/pdf')})
        return True
    except Exception as exc:
        _log.warning("Could not send invoice PDF for order %s: %s", order_id, exc)
        return False


def _is_authorized(chat_id: int) -> bool:
    """Return True if chat_id is the bot admin, in the dashboard manager whitelist,
    or is a user-type destination registered via /add."""
    if chat_id == ADMIN_CHAT_ID:
        return True
    try:
        cfg = _load_settings_json()
        ids = [int(x) for x in cfg.get('telegram_manager_ids', []) if str(x).strip()]
        if chat_id in ids:
            return True
    except Exception:
        pass
    # Also allow anyone registered as an individual user destination via /add
    try:
        for d in load_destinations():
            if d.get('type') in ('user', 'private') and int(d['chat_id']) == chat_id:
                return True
    except Exception:
        pass
    return False


def _handle_admin_search(chat_id: int, text: str):
    """Search orders and reply with PDF invoice first, then order summary + tracking."""
    query = _normalize_query(text.lstrip('#'))
    if not query:
        return

    try:
        _dash = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard')
        if _dash not in sys.path:
            sys.path.insert(0, _dash)
        import settings_manager as _sm_mod
        import hub_client as _hc

        cfg = _sm_mod.load()
        hub_url = cfg.get('hub_url', 'http://127.0.0.1:8090')
        hub_key = cfg.get('hub_api_key', '')

        result = _hc.list_orders(hub_url, hub_key, search=query, per_page=3)
        if not result or result.get('error') or not result.get('data'):
            _send_msg(chat_id, "سفارشی یافت نشد.")
            return

        orders = result['data']
        total = result.get('pagination', {}).get('total', len(orders))

        if total > 3:
            _send_msg(chat_id, f"🔍 <b>{total} نتیجه برای «{query}» — ۳ مورد اخیر:</b>")

        for order in orders[:3]:
            oid = order.get('id')
            full = _hc.get_order_detail(hub_url, hub_key, oid)
            if not full:
                continue

            tracking = _hc.extract_tracking(full)
            billing = full.get('billing') or {}
            customer = f"{billing.get('first_name') or ''} {billing.get('last_name') or ''}".strip() or '—'
            phone = billing.get('phone') or '—'
            status_label = _STATUS_LABELS.get(full.get('status', ''), full.get('status', ''))
            amount = _fmt(full.get('total', 0))

            tcode = tracking.get('tracking_code')
            tracking_line = (f"📮 رهگیری: <code>{tcode}</code>"
                             if tcode else f"📦 {tracking.get('message', 'در حال پردازش')}")

            # One combined message: PDF with caption (or text-only if no PDF available)
            caption = (
                f"<b>سفارش #{oid}</b>\n"
                f"وضعیت: {status_label}\n"
                f"مشتری: {customer} | {phone}\n"
                f"مبلغ: {amount} تومان\n"
                f"{tracking_line}"
            )[:1024]

            sent_pdf = _send_invoice_for_admin(chat_id, full, oid, caption=caption)
            if not sent_pdf:
                _send_msg(chat_id, caption)

    except Exception as exc:
        _log.error("admin_search error: %s", exc, exc_info=True)
        _send_msg(chat_id, f"⚠️ خطا در جستجو: {exc}")


def _handle_command(msg: dict):
    chat_id: int = msg['chat']['id']
    if not _is_authorized(chat_id):
        _send_msg(chat_id, "⛔ دسترسی غیرمجاز.")
        return

    text_raw = msg.get('text', '').strip()
    parts = text_raw.split(maxsplit=3)
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

    elif text_raw.startswith('/'):
        _send_msg(chat_id,
            "دستورات موجود:\n"
            "/menu — منوی اصلی\n"
            "/add &lt;chat_id&gt; &lt;name&gt; &lt;type&gt; — افزودن مقصد\n"
            "/remove &lt;chat_id&gt; — حذف مقصد\n"
            "/list — لیست مقصدها\n"
            "/status — وضعیت کلی\n\n"
            "یا شماره سفارش، موبایل، یا نام مشتری را مستقیم ارسال کنید."
        )

    else:
        # Plain text from admin: treat as order search
        _handle_admin_search(chat_id, text_raw)


def _handle_callback(cb: dict):
    chat_id: int = cb['from']['id']
    _api('answerCallbackQuery', json={'callback_query_id': cb['id']})

    if not _is_authorized(chat_id):
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


def start_bot_polling():
    """Start the bot long-polling loop in a background daemon thread.

    Called from run_dashboard.py so the bot runs inside the dashboard process.
    """
    import threading

    def _runner():
        try:
            run_bot()
        except Exception as exc:
            _log.error("Bot polling thread exited unexpectedly: %s", exc)

    t = threading.Thread(target=_runner, name='tg-bot-poll', daemon=True)
    t.start()
    _log.info("Telegram bot polling thread started (admin=%s).", ADMIN_CHAT_ID)
    return t


def _drain_pending_updates() -> int:
    """Discard any updates queued before this session. Returns starting offset."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{_TOKEN}/getUpdates",
            params={'timeout': 0},
            timeout=10,
        )
        data = r.json()
        if data.get('ok') and data.get('result'):
            latest = data['result'][-1]['update_id']
            _log.info("Drained %d pending update(s) on startup.", len(data['result']))
            return latest + 1
    except Exception as exc:
        _log.warning("Could not drain pending updates: %s", exc)
    return None


def run_bot():
    """Long-polling bot loop for admin destination management."""
    if not _TOKEN:
        print("Error: TG_BOT_TOKEN is not set in .env")
        sys.exit(1)

    print(f"Bot started. Listening for admin (ID: {ADMIN_CHAT_ID})...")
    # Discard messages queued before this session to avoid replay loops
    offset: int | None = _drain_pending_updates()

    while True:
        try:
            params: dict = {
                'timeout': 30,
                'allowed_updates': ['message', 'callback_query'],
            }
            if offset is not None:
                params['offset'] = offset

            # HTTP timeout must exceed Telegram's long-poll timeout
            r = requests.get(
                f"https://api.telegram.org/bot{_TOKEN}/getUpdates",
                params=params,
                timeout=40,
            )
            result = r.json() if r.ok else None
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
