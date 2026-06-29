# WooCommerce Orders — Telegram Notifier

Receives order events from the Behdashtik Hub, generates PDF invoices, and sends Telegram notifications. Supports both Basalam-origin orders and standard WooCommerce orders, with a web dashboard for configuration and monitoring.

## Architecture

```
WooCommerce / WordPress site
        ↓  (webhook)
Behdashtik Hub  (/root/behdashtik-hub-main)
        ↓  POST /webhook/hub-order  (HMAC-signed, port 5100)
webhook_server.py
        ↓
process_order.py  ←→  order_state.py (SQLite)
        ↓                     ↓
pdf_generator.py        telegram_notify.py
```

**This app must never connect directly to WordPress or WooCommerce.**
All order data arrives exclusively through the Hub webhook.

The dashboard (`run_dashboard.py`, port 8000) runs alongside the webhook server and bot polling thread, providing admin UI for configuration and order monitoring.

## Project files

```
.
├── webhook_server.py         # Flask HTTP server: POST /webhook/hub-order (port 5100)
├── process_order.py          # Core logic: detect → PDF → Telegram → state
├── basalam_detect.py         # Basalam order detection from Hub payload fields
├── pdf_generator.py          # PDF invoice generation (Persian/RTL, Jalali dates)
├── order_state.py            # SQLite state: order status, sent Telegram message IDs
├── telegram_notify.py        # Telegram notifications + admin bot + order search
├── hub_adapter.py            # Programmatic entry point (thin wrapper over process_order)
├── backup.py                 # Bulk PDF export stub — data must come through the Hub
├── run_dashboard.py          # Starts the dashboard on port 8000
├── dashboard/
│   ├── app.py                # Flask dashboard application
│   ├── auth.py               # Session auth (bcrypt passwords)
│   ├── hub_client.py         # Hub Data API client + tracking extraction
│   ├── settings_manager.py   # Load/save dashboard/data/settings.json
│   ├── scheduler.py          # APScheduler for daily reports
│   ├── daily_report.py       # Daily order/sales report builder
│   ├── templates/            # Dashboard Jinja2 templates
│   └── static/               # CSS / JS assets
├── templates/                # Jinja2 HTML templates for PDF invoice
├── sample_order.json         # Test order in Hub webhook payload format
├── test_flow.py              # Dry-run integration test (no Telegram, no WeasyPrint)
├── .env.example              # All environment variables with documentation
└── requirements.txt
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your values
```

**Persian font required.** Download [Vazirmatn](https://github.com/rastikerdar/vazirmatn) (or any Persian `.ttf`) and set `FONT_PATH` in `.env`.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `FONT_PATH` | Yes | — | Path to a Persian `.ttf` font |
| `STORE_NAME` | Yes | — | Store name shown in PDF header |
| `STORE_PHONE` | Yes | — | Store phone |
| `STORE_ADDRESS` | Yes | — | Store address |
| `STORE_POSTCODE` | Yes | — | Store postcode |
| `SITE_URL` | Yes | — | Store URL |
| `TG_BOT_TOKEN` | For notifications | — | Telegram bot token |
| `TG_ADMIN_ID` | No | `213946880` | Telegram chat ID of the bot admin |
| `TG_DESTINATIONS_FILE` | No | `telegram_destinations.json` | Notification destinations config |
| `TARGET_ORDER_STATUSES` | No | see below | Comma-separated status slugs that trigger notifications |
| `ORDER_STATE_DB` | No | `./data/order_state.sqlite3` | SQLite state file path |
| `STATE_RETENTION_DAYS` | No | `30` | Days to keep order records |
| `WEBHOOK_SECRET` | Yes | — | HMAC secret matching the Hub's `woo-orders` endpoint secret |
| `WEBHOOK_PORT` | No | `5100` | Port for the webhook HTTP server (must not be 5000) |
| `DASHBOARD_PORT` | No | `8000` | Port for the admin dashboard |

## Running

### Webhook server (receives events from Hub)

```bash
/usr/bin/python3 webhook_server.py
```

Listens on `http://localhost:5100/webhook/hub-order`. Verifies HMAC-SHA256 signature from the Hub. Handles two events:
- `order.upserted` → calls `process_order(data)`
- `order.deleted` → deletes previous Telegram messages and clears state

### Admin dashboard

```bash
python run_dashboard.py
```

Starts the dashboard on port 8000 (proxied by nginx at `/orders-api`). Also launches:
- The daily-report APScheduler in the background
- The Telegram admin bot polling thread

Dashboard sections:
- **Overview** — Hub and webhook server health, recent webhook log
- **Orders** — Live order list from Hub API with status, amounts, customer info
- **Telegram** — Bot config, notification destination management, manager IDs, test send, message templates
- **Settings** — Hub API URL/key, basalam_only mode, PDF settings
- **Stock** — Product stock levels via Hub API
- **Daily Report** — Order/sales summary for any date range

## Dashboard settings (`dashboard/data/settings.json`)

Managed via the Settings page; do not edit by hand unless the dashboard is down.

| Key | Type | Default | Description |
|---|---|---|---|
| `hub_url` | string | `http://127.0.0.1:8090` | Hub Data API base URL |
| `hub_api_key` | string | `""` | Hub API key |
| `basalam_only` | bool | `false` | When `true`, only Basalam-origin orders are processed; all others are silently skipped |
| `send_pdf_with_new_order` | bool | `true` | Attach PDF invoice to order notifications |
| `telegram_manager_ids` | list[int] | `[]` | Extra Telegram user IDs that receive notifications (no message-ID tracking) |
| `templates.new_order` | string | — | Template for standard order notifications (supports `{order_id}`, `{customer_name}`, `{total}`, etc.) |
| `templates.basalam_order` | string | — | Template for Basalam order notifications (also supports `{basalam_fee}`, `{basalam_net}`, `{basalam_purchase_count}`) |
| `daily_report.send_time` | string | `"23:55"` | Time (HH:MM) to send the daily report |

## Order processing logic (`process_order.py`)

For each incoming order:
1. If `basalam_only=true`, non-Basalam orders are skipped (`skipped_reason: basalam_only_mode`)
2. If `order.status` (after normalising `wc-` prefix) is not in `TARGET_ORDER_STATUSES` → skipped
3. If this exact order+status was already notified → skipped (deduplication)
4. If `send_pdf_with_new_order=true` → generate PDF invoice
5. Send Telegram notification to all configured destinations and manager IDs
6. Persist state in SQLite for future deduplication and message-ID tracking

On status change: the previous bot message is deleted (best-effort, within Telegram's 48-hour window) and a new message is sent.

## Basalam order detection (`basalam_detect.py`)

Detection checks in priority order:
1. `order_source == "basalam"` — Hub computes this from `_sync_basalam_hash_id` meta key
2. `_sync_basalam_hash_id` present in `meta_data` array
3. `payment_method` starts with `"basalam"`

## Basalam status mapping

Real statuses confirmed from mirror DB (June 2026). Hub strips the `wc-` prefix before sending the webhook payload. The app also normalises raw `wc-*` statuses as a safety net.

| DB slug | Webhook slug | Persian label |
|---|---|---|
| `wc-bslm-preparation` | `bslm-preparation` | باسلام — آماده‌سازی سفارش |
| `wc-bslm-shipping` | `bslm-shipping` | باسلام — سفارش برای مشتری |
| `wc-bslm-completed` | `bslm-completed` | باسلام — تکمیل‌شده |
| `wc-bslm-rejected` | `bslm-rejected` | باسلام — لغو شده |
| `wc-bslm-wait-vendor` | `bslm-wait-vendor` | باسلام — انتظار فروشنده |
| `wc-refunded` | `refunded` | مسترد شده |

Default `TARGET_ORDER_STATUSES` includes all active Basalam slugs plus `processing` and `ready-to-ship` for standard WooCommerce orders. `bslm-completed` is intentionally excluded (completed orders generate no notification).

## Hub API client (`dashboard/hub_client.py`)

All order/product data fetched by the dashboard goes through `hub_client.py`, never directly to WordPress or WooCommerce.

| Function | Endpoint | Description |
|---|---|---|
| `health(hub_url, api_key)` | `GET /api/v1/health` | Hub health check |
| `list_orders(...)` | `GET /api/v1/orders` | Paginated order list; supports status, order_source, date range, search |
| `get_order_detail(hub_url, api_key, order_id)` | `GET /api/v1/orders/{id}` | Full order detail |
| `list_products(...)` | `GET /api/v1/products` | Product list with optional stock_status filter |
| `orders_summary(...)` | `GET /api/v1/analytics/orders-summary` | Aggregated totals for date range |
| `sync_status(hub_url, api_key)` | `GET /api/v1/sync/status` | Hub sync status |
| `extract_tracking(order)` | — | Parse postal tracking code from order meta (pure function, no HTTP) |

Hub requests use `X-Hub-API-Key` header authentication.

## Telegram admin bot

The bot runs as a polling thread inside the dashboard process (started by `run_dashboard.py`). It can also be started standalone:

```bash
python telegram_notify.py bot
```

Only the admin defined by `TG_ADMIN_ID` and users listed in `telegram_manager_ids` can interact with the bot.

| Command / Input | Description |
|---|---|
| `/menu` | Open interactive button menu |
| `/add <chat_id> <name> <type>` | Add destination (`type`: `user` / `group` / `channel`) |
| `/remove <chat_id>` | Remove a destination |
| `/list` | List all destinations |
| `/status` | Show current config summary |
| Any text / order number | Search orders via Hub API; returns PDF invoice + tracking info |

## CLI / manual test

```bash
python process_order.py
python process_order.py path/to/order.json
```

## Dry-run validation

```bash
python test_flow.py
python test_flow.py --verbose
```

No Telegram messages sent, no WeasyPrint/font required. Exercises six scenarios:
1. Basalam + `bslm-preparation` → notify, store state + message_id
2. Status advance to `bslm-shipping` → delete old message, send new
3. Basalam + non-target status (`pending`) → skip silently
4. Non-Basalam order when `basalam_only=True` → skip (`basalam_only_mode`)
5. Basalam + `bslm-completed` → skip silently (not in TARGET_ORDER_STATUSES)
6. Basalam + `bslm-preparation` new order → notified normally

## Hub integration rules

**Do not connect directly to WordPress or WooCommerce.** All data must arrive through the Hub.

The Hub (`/root/behdashtik-hub-main`) may only be modified when:
- A communication path needs to be created or fixed (e.g. missing fields in the webhook payload)
- A confirmed integration bug prevents this app from receiving correct data

Any Hub change must be minimal, scoped, and reported. The `_build_order_payload` function in the Hub's pipeline is the authoritative place where order data is prepared for this app.

## Limitations

- PDF generation requires WeasyPrint system dependencies and a Persian font.
- Telegram message deletion only works within the 48-hour Telegram bot window.
- `telegram_destinations.json` is gitignored (runtime config); back it up separately.
- `data/` (SQLite state) is gitignored; back it up on the server.
