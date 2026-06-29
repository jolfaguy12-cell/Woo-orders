# Tracking API — Postal Tracking Code Retrieval

Returns the postal tracking code and shipment status for any WooCommerce order.

---

## Endpoint

```
GET /orders-api/api/v1/tracking/{order_id}
```

- **Base URL (production):** `https://support.behdashtik.ir`
- **Full URL example:** `https://support.behdashtik.ir/orders-api/api/v1/tracking/6305`

---

## Authentication

Uses the same key as the Invoice API.  
Pass it in the request header:

```
X-Invoice-API-Key: <your-key>
```

The key is managed from the **Settings** page → **Invoice API** card.

---

## Request

| Field  | Value |
|--------|-------|
| Method | `GET` |
| Path   | `/orders-api/api/v1/tracking/<order_id>` |
| Header | `X-Invoice-API-Key: <key>` |
| Body   | none |

---

## Response — Success (200)

```json
{
  "ok": true,
  "data": {
    "order_id": 6305,
    "order_status": "bslm-shipping",
    "status": "shipped",
    "status_label": "ارسال شده",
    "tracking_code": "12345678901234",
    "message": "کد رهگیری: 12345678901234"
  }
}
```

### `status` values

| `status`      | Meaning |
|---------------|---------|
| `shipped`     | Order shipped; `tracking_code` contains the postal code |
| `in_progress` | Order paid and being processed; not yet shipped |
| `unpaid`      | Order not yet paid (`pending` or `failed`) |
| `cancelled`   | Order cancelled, rejected, or refunded |

When `status` is not `shipped`, `tracking_code` is `null`.

---

## Response — Error

All errors return JSON:

```json
{"error": "<code>", "code": <http_status>}
```

| HTTP Status | `error` field      | Cause |
|-------------|--------------------|-------|
| 401         | `unauthorized`     | Missing/invalid `X-Invoice-API-Key` |
| 404         | `order_not_found`  | No order with that ID found in Hub |
| 500         | `internal_error`   | Hub connectivity failure |

---

## curl Examples

### Get tracking for order 6305

```bash
curl -s -H "X-Invoice-API-Key: YOUR_KEY_HERE" \
     https://support.behdashtik.ir/orders-api/api/v1/tracking/6305 \
     | python3 -m json.tool
```

### Test locally

```bash
KEY=$(python3 -c "
import sys; sys.path.insert(0,'dashboard')
import settings_manager; print(settings_manager.load().get('invoice_api_key',''))
")

curl -s -H "X-Invoice-API-Key: $KEY" \
     http://127.0.0.1:8000/orders-api/api/v1/tracking/6305 \
     | python3 -m json.tool
```

---

## Tracking Code Sources

The API checks the following WooCommerce order meta keys (in order of priority):

1. `_tracking_number`
2. `_woo_shiment_tracking_number`
3. `_aftership_tracking_number`
4. `_tracking_code`
5. `_order_tracking_number`
6. `tracking_number`
7. `_yith_shipment_tracking_number`
8. `_wc_shipment_tracking_items` (PHP-serialized WooCommerce Shipment Tracking plugin format)

The first non-empty value found is returned as `tracking_code`.
