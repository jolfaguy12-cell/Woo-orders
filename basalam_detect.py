import os

_BASALAM_META_KEYS = set(
    os.getenv(
        'BASALAM_META_KEYS',
        '_order_source,source,channel,known_source,_basalam_order_id'
    ).split(',')
)
_KEYWORDS = {'basalam'}


def is_basalam_order(order: dict) -> bool:
    """
    Return True if the order originates from Basalam.

    Detection strategy (checked in order):
      1. `created_via` field contains a basalam keyword.
      2. Any meta key name itself contains a basalam keyword.
      3. A meta key matching BASALAM_META_KEYS has a value containing a keyword.
      4. Any meta value contains a basalam keyword (catch-all for unknown field names).

    Adjust BASALAM_META_KEYS env var to add/remove watched key names.
    """
    created_via = order.get('created_via', '').lower()
    if any(kw in created_via for kw in _KEYWORDS):
        return True

    for meta in order.get('meta_data', []):
        key = str(meta.get('key', '')).lower().strip()
        value = str(meta.get('value', '')).lower().strip()

        if any(kw in key for kw in _KEYWORDS):
            return True

        if key in _BASALAM_META_KEYS and any(kw in value for kw in _KEYWORDS):
            return True

        if any(kw in value for kw in _KEYWORDS):
            return True

    return False
