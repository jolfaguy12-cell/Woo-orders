# CLAUDE.md — WooCommerce Orders 

Read this file first before exploring or modifying anything in this project.

---

## Project Overview

This service reads WordPress/WooCommerce orders from the central Data Hub.
It must not connect directly to WordPress or WooCommerce.
It processes orders and sends Telegram notifications to managers, especially Basalam-origin orders.
If an order is updated, it can delete/replace the previous Telegram message and update the order status through the approved hub workflow.
It will also send automatic daily reports about orders, sales, shipping, and operational status.

## Server Paths

| Item | Path |
|---|---|
| This project | `/root/production-site/woocomerce-orders` |
| Hub / Data Hub | `/root/behdashtik-hub-main` |

---

## Connection Rule: Hub/Data Hub Only

**All order data enters this project exclusively through the Behdashtik Hub/Data Hub.**

The normal data flow is:

```
WooCommerce / WordPress site
        ↓  (webhook)
Behdashtik Hub / Data Hub  (/root/behdashtik-hub-main)
        ↓  POST /webhook/hub-order  (HMAC-signed, port 5100)
webhook_server.py
        ↓
process_order.py
        ↓              ↓
pdf_generator.py   telegram_notify.py
```

`webhook_server.py` and `process_order.py` are the primary runtime entry points.
`hub_adapter.py` provides a programmatic entry point for direct Python imports from the Hub.
The Hub owns the WooCommerce webhook endpoint and HMAC authentication; this service only receives the already-validated payload.

---

## Forbidden: No Direct WordPress / WooCommerce REST API

**This project must never: Connects To Production Site : behdashtik.ir Directly**


## Hub / Data Hub Reference

To understand the integration layer, inspect:

/root/behdashtik-hub-main

Do not modify the hub under normal circumstances.

The hub may only be modified if it is strictly necessary to create or adjust the communication path between this service and the hub, or to debug a confirmed integration/connection issue that prevents this service from receiving data correctly.

Any hub change must be minimal, scoped, and clearly reported.