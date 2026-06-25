"""
Bulk PDF generator: fetches all orders from the WooCommerce REST API
and generates invoice PDFs (packing slip skipped for speed).

Usage:
    python backup.py

Requires WC_URL, WC_KEY, WC_SECRET in .env.
Output goes to ./backup_pdfs/; a zip archive is created when done.
"""

import os
import shutil
import time
from dotenv import load_dotenv
from woocommerce import API

load_dotenv()

from pdf_generator import generate_pdf


def backup_orders():
    wc_url = os.getenv("WC_URL")
    wc_key = os.getenv("WC_KEY")
    wc_secret = os.getenv("WC_SECRET")

    if not all([wc_url, wc_key, wc_secret]):
        print("Error: WC_URL, WC_KEY, WC_SECRET must be set in .env")
        return

    wcapi = API(
        url=wc_url,
        consumer_key=wc_key,
        consumer_secret=wc_secret,
        version="wc/v3",
        timeout=30,
    )

    backup_dir = "backup_pdfs"
    os.makedirs(backup_dir, exist_ok=True)

    page = 1
    per_page = 20
    total = 0

    print(f"Connecting to {wc_url}...")

    while True:
        print(f"Fetching page {page}...")
        try:
            res = wcapi.get("orders", params={"per_page": per_page, "page": page})
            res.raise_for_status()
            orders = res.json()
        except Exception as e:
            print(f"Failed to fetch orders: {e}")
            break

        if not orders:
            print("No more orders.")
            break

        for order in orders:
            try:
                generate_pdf(order, output_dir=backup_dir, skip_packing_slip=True)
                total += 1
            except Exception as e:
                print(f"Failed for order {order.get('id')}: {e}")

        page += 1
        time.sleep(2)

    if total > 0:
        zip_name = "woocommerce_backup"
        shutil.make_archive(zip_name, 'zip', backup_dir)
        print(f"Done. {total} PDFs archived as {zip_name}.zip")
    else:
        print("No orders downloaded.")


if __name__ == "__main__":
    backup_orders()
