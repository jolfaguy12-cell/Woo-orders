"""
Basalam order detection.

A Basalam order is a normal WooCommerce order synced from Basalam marketplace.
Detection uses concrete WooCommerce/Hub fields, not keyword heuristics.

Priority (highest to lowest):
  1. order_source == 'basalam'   — Hub computes this from _sync_basalam_hash_id
  2. _sync_basalam_hash_id key in meta_data  — present on every Basalam order
  3. payment_method starts with 'basalam'    — fallback for raw WC payloads
"""


def is_basalam_order(order: dict) -> bool:
    # Hub webhook payload: order_source field
    if order.get('order_source') == 'basalam':
        return True

    # meta_data array (WC REST API format or Hub webhook with meta_data included)
    for item in order.get('meta_data', []):
        if str(item.get('key', '')) == '_sync_basalam_hash_id' and item.get('value'):
            return True

    # Basalam payment method (distinctive across all Basalam orders)
    pm = str(order.get('payment_method') or '').lower()
    if pm.startswith('basalam'):
        return True

    return False
