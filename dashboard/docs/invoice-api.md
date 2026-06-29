# Invoice API — External PDF Retrieval

Allows an external application to fetch a PDF invoice for any order by its WooCommerce order ID.

---

## Endpoint

```
GET /orders-api/api/v1/invoice/{order_id}
```

- **Base URL (production):** `https://support.behdashtik.ir`
- **Full URL example:** `https://support.behdashtik.ir/orders-api/api/v1/invoice/6305`

---

## Authentication

The endpoint does **not** use dashboard session cookies.  
Pass the API key in the request header:

```
X-Invoice-API-Key: <your-key>
```

The key is generated (or regenerated) from the **Settings** page → **Invoice API** card.  
If the key is empty (never generated), all requests are rejected with 401.

---

## Request

| Field  | Value |
|--------|-------|
| Method | `GET` |
| Path   | `/orders-api/api/v1/invoice/<order_id>` |
| Header | `X-Invoice-API-Key: <key>` |
| Body   | none |

---

## Response — Success (200)

Returns the PDF binary with:

```
Content-Type: application/pdf
Content-Disposition: attachment; filename="invoice_<order_id>.pdf"
```

The PDF contains only the invoice (packing slip is skipped).

---

## Response — Error

All errors return JSON:

```json
{"error": "<code>", "code": <http_status>}
```

| HTTP Status | `error` field      | Cause |
|-------------|--------------------|-------|
| 401         | `unauthorized`     | Missing/invalid `X-Invoice-API-Key`, or key not yet generated |
| 404         | `order_not_found`  | No order with that ID found in Hub |
| 500         | `internal_error`   | PDF generation or Hub connectivity failure |

---

## curl Examples

### Fetch invoice for order 6305

```bash
curl -H "X-Invoice-API-Key: YOUR_KEY_HERE" \
     https://support.behdashtik.ir/orders-api/api/v1/invoice/6305 \
     -o invoice_6305.pdf \
     -w "\nHTTP status: %{http_code}\n"
```

### Test locally (dashboard running on port 8000)

```bash
# Retrieve key from settings file
KEY=$(python3 -c "
import sys; sys.path.insert(0,'dashboard')
import settings_manager; print(settings_manager.load().get('invoice_api_key',''))
")

curl -H "X-Invoice-API-Key: $KEY" \
     http://127.0.0.1:8000/orders-api/api/v1/invoice/6305 \
     -o /tmp/test_invoice.pdf \
     -w "\nHTTP: %{http_code}\n"
```

### Check error response

```bash
curl -s -H "X-Invoice-API-Key: wrong-key" \
     http://127.0.0.1:8000/orders-api/api/v1/invoice/6305
# {"error":"unauthorized","code":401}
```

---

## Notes

- The invoice PDF is **cached** on disk (`output/` dir) after first generation. Subsequent calls for the same order are served from the cached file (< 5ms). A cache miss triggers on-demand generation.
- PDFs older than 60 days are automatically deleted by a nightly cleanup job (03:00 Tehran time). A subsequent API call will regenerate.
- The packing slip is always skipped (invoice only).
- The "Attach PDF invoice" dashboard toggle only affects Telegram order notifications. The invoice API always returns a PDF regardless of that setting.
- PDF layout, store name, and store address come from `.env` (`STORE_NAME`, `STORE_PHONE`, `STORE_ADDRESS`).
- Regenerating the key immediately invalidates the previous key.
