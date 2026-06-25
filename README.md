# WooCommerce Order Processor

Generates PDF invoices and packing slips from WooCommerce order JSON, and sends Telegram notifications when Basalam-origin orders reach configured statuses.

Designed to be called by an external webhook hub (`wordpress-data-hub`), not to run its own web server.

## Project structure

```
.
├── process_order.py      # Reusable entrypoint: process_order(order: dict)
├── pdf_generator.py      # PDF invoice + packing slip (Persian/RTL, Jalali dates)
├── basalam_detect.py     # Basalam order origin detection
├── order_state.py        # SQLite state: order status, sent message IDs
├── telegram_notify.py    # Telegram notifications + admin bot
├── backup.py             # Bulk PDF export via WooCommerce REST API
├── templates/
│   ├── invoice.html
│   ├── packing_slip.html
│   └── style.css
├── sample_order.json     # Minimal test order (Basalam origin, processing status)
├── .env.example
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
| `STORE_NAME` | Yes | — | Store name shown in PDF |
| `STORE_PHONE` | Yes | — | Store phone |
| `STORE_ADDRESS` | Yes | — | Store address |
| `STORE_POSTCODE` | Yes | — | Store postcode |
| `SITE_URL` | Yes | — | Store URL |
| `TG_BOT_TOKEN` | For notifications | — | Telegram bot token |
| `TG_DESTINATIONS_FILE` | No | `telegram_destinations.json` | Notification destinations config |
| `TARGET_STATUSES` | No | `processing,wc-ready-to-ship` | Comma-separated WooCommerce status slugs |
| `BASALAM_META_KEYS` | No | `_order_source,source,channel,known_source,_basalam_order_id` | Meta keys checked for Basalam value |
| `STATE_DB_PATH` | No | `order_state.db` | SQLite state file path |
| `STATE_RETENTION_DAYS` | No | `30` | Days to keep order records |
| `WC_URL` / `WC_KEY` / `WC_SECRET` | `backup.py` only | — | WooCommerce REST API credentials |

## Usage

### Process a single order (CLI / manual test)

```bash
python process_order.py sample_order.json
```

### Call from code

```python
from process_order import process_order

result = process_order(order_dict)
# returns: {order_id, status, pdf, notified, skipped_reason}
```

`process_order` will:
1. Skip if the order is not Basalam-origin.
2. Skip if the status is not in `TARGET_STATUSES`.
3. Skip if this order+status combination was already notified.
4. Generate the PDF.
5. Send the PDF to all configured Telegram destinations.
6. On status change: delete the previous bot message, then send the updated one.

### Telegram admin bot

Start long-polling bot (keep running in the background):

```bash
python telegram_notify.py bot
```

The bot admin is hardcoded to chat ID `213946880`. Only this user can manage destinations.

**Commands:**

| Command | Description |
|---|---|
| `/menu` | Interactive menu with inline buttons |
| `/add <chat_id> <name> <type>` | Add destination (`type`: `user` / `group` / `channel`) |
| `/remove <chat_id>` | Remove a destination |
| `/list` | List all destinations |
| `/status` | Show current config summary |

**Example:**
```
/add -1001234567890 گروه مدیران group
/add 987654321 مدیر فروش user
```

Destinations are stored in `telegram_destinations.json` (gitignored).

### Bulk backup (all WooCommerce orders → PDFs)

```bash
python backup.py
```

Requires `WC_URL`, `WC_KEY`, `WC_SECRET`. Output goes to `backup_pdfs/` and is zipped.

## Order notification rules

- Notifies only when `is_basalam_order(order) == True` **and** `order['status']` is in `TARGET_STATUSES`.
- On re-notification (status changed): best-effort deletes the previous message, then sends the updated PDF. Telegram allows deletion only within 48 hours; older messages silently fail.

## Basalam detection (`basalam_detect.py`)

Checks in order:
1. `created_via` field contains `"basalam"`.
2. Any `meta_data` key name contains `"basalam"`.
3. Any key in `BASALAM_META_KEYS` has a value containing `"basalam"`.
4. Any `meta_data` value contains `"basalam"` (catch-all).

Adjust `BASALAM_META_KEYS` in `.env` to match your store's meta field names.

## WordPress Data Hub integration

This project does not run a webhook server. It exposes `process_order(order: dict)` for the `wordpress-data-hub` to call after receiving and validating incoming WooCommerce webhooks.

A local cache of recent orders can be stored in `wordpress-data-hub/cache/` (excluded from git via `.gitignore`).

## Limitations

- No web server; must be invoked externally or via CLI.
- The Telegram admin bot must be running separately (`python telegram_notify.py bot`) to accept management commands.
- PDF generation requires WeasyPrint system dependencies. See [WeasyPrint docs](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#installation).
- Persian font is not bundled; must be supplied separately.
- Telegram message deletion works only within the 48-hour window imposed by Telegram for bots.
