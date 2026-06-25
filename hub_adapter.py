"""
WordPress Data Hub adapter — placeholder.

This project does not run its own webhook server.
The `wordpress-data-hub` (located at ../wordpress-data-hub/ on the server)
owns the WooCommerce webhook endpoint and forwards validated order dicts here.

To wire up the hub, register a handler in the hub that calls:

    from woo_orders.process_order import process_order
    result = process_order(order_dict)

Or, if running as a subprocess / via file import:

    import subprocess, json
    subprocess.run(
        ["python", "/path/to/woo-orders/process_order.py"],
        input=json.dumps(order_dict),
        ...
    )

The hub is responsible for:
  - Receiving and authenticating the WooCommerce webhook
  - Parsing the raw payload into a WooCommerce order dict
  - Calling process_order(order_dict) for each incoming order or status update
  - Optionally caching recent orders in wordpress-data-hub/cache/ (gitignored)

This file is intentionally minimal. Expand it when the hub integration is built.
"""


def handle_order_from_hub(order: dict) -> dict:
    """
    Thin wrapper so the hub can import a single stable function name.
    Returns the result dict from process_order.
    """
    from process_order import process_order
    return process_order(order)
