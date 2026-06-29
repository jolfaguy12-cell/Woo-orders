"""
Bulk PDF generator stub.

This project no longer connects directly to the WooCommerce REST API.
Bulk order data must come from the wordpress-data-hub, which owns the
WooCommerce connection and can replay or batch-deliver order dicts to
process_order.process_order(order_dict).

To run a bulk export, have the hub deliver orders in a loop:

    from process_order import process_order

    for order_dict in hub.get_orders_batch(...):
        process_order(order_dict)

Output will go to ./output/ as usual.
"""


def backup_orders():
    raise NotImplementedError(
        "Direct WooCommerce REST API access has been removed. "
        "Request bulk orders from the wordpress-data-hub instead."
    )


if __name__ == "__main__":
    backup_orders()
