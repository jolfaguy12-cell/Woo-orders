import glob
import os
import re
import time
from datetime import datetime
import jdatetime
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from dotenv import load_dotenv

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_OUTPUT_ROOT = os.path.join(_PROJECT_ROOT, 'output')

load_dotenv(os.path.join(_PROJECT_ROOT, '.env'))

FONT_PATH = os.getenv('FONT_PATH', '')
STORE_NAME = os.getenv('STORE_NAME', 'نام فروشگاه')
STORE_PHONE = os.getenv('STORE_PHONE', 'تلفن فروشگاه')
STORE_ADDRESS = os.getenv('STORE_ADDRESS', 'آدرس فروشگاه')
STORE_POSTCODE = os.getenv('STORE_POSTCODE', 'کد پستی فروشگاه')
SITE_URL = os.getenv('SITE_URL', 'https://yoursite.com').rstrip('/')

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')


def _parse_order_date(date_str: str) -> jdatetime.date:
    try:
        dt = datetime.strptime(date_str.split('T')[0], '%Y-%m-%d')
        return jdatetime.date.fromgregorian(date=dt)
    except Exception:
        return jdatetime.date.today()


def _format_currency(value) -> str:
    try:
        return "{:,.0f}".format(float(value))
    except (ValueError, TypeError):
        return str(value)


def _render(template_name: str, context: dict) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR))
    env.filters['currency'] = _format_currency
    return env.get_template(template_name).render(context)


def generate_pdf(order: dict, output_dir: str = None) -> str:
    """Generate a PDF sales invoice for the given order dict. Returns the output path."""
    order = dict(order)  # avoid mutating caller's dict
    jalali_date = _parse_order_date(order.get('date_created', ''))
    order['jalali_date'] = jalali_date.strftime('%Y/%m/%d')

    # Hub API uses tax_total; template expects total_tax
    if 'total_tax' not in order:
        order['total_tax'] = order.get('tax_total', 0) or 0

    # Ensure each line_item has a unit 'price' field and integer quantity
    line_items = [dict(item) for item in order.get('line_items', [])]
    for item in line_items:
        if 'price' not in item or item['price'] is None:
            qty = float(item.get('quantity') or 1)
            item['price'] = float(item.get('total') or 0) / qty if qty else 0.0
        qty_val = item.get('quantity')
        if qty_val is not None:
            qty_f = float(qty_val)
            item['quantity'] = int(qty_f) if qty_f == int(qty_f) else qty_f
    order['line_items'] = line_items

    # Read store config from env at call time so dashboard edits take effect immediately
    store = {
        'name':     os.getenv('STORE_NAME',     STORE_NAME),
        'phone':    os.getenv('STORE_PHONE',    STORE_PHONE),
        'address':  os.getenv('STORE_ADDRESS',  STORE_ADDRESS),
        'postcode': os.getenv('STORE_POSTCODE', STORE_POSTCODE),
        'url':      os.getenv('SITE_URL',       SITE_URL).rstrip('/'),
    }

    css_ctx = {'font_path': os.path.abspath(os.getenv('FONT_PATH', FONT_PATH)) if os.getenv('FONT_PATH', FONT_PATH) else ''}
    css = _render('style.css', css_ctx)

    invoice_html = _render('invoice.html', {'order': order, 'store': store})
    final_html = f"<html><head><style>{css}</style></head><body>{invoice_html}</body></html>"

    year = jalali_date.strftime('%Y')
    month = jalali_date.strftime('%m')
    day = jalali_date.strftime('%d')
    order_id = order.get('id', 'UNKNOWN')

    if not output_dir:
        output_dir = os.path.join(_DEFAULT_OUTPUT_ROOT, year, month)
    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, f"{year}-{month}-{day}_{order_id}.pdf")
    HTML(string=final_html, base_url=TEMPLATES_DIR).write_pdf(output_path)
    print(f"PDF generated: {output_path}")
    return output_path


def find_invoice_pdf(order_id: int, output_root: str = None) -> str | None:
    """Return the path of the most recent cached PDF for order_id, or None."""
    root = output_root or _DEFAULT_OUTPUT_ROOT
    matches = glob.glob(os.path.join(root, '**', f'*_{order_id}.pdf'), recursive=True)
    return max(matches, key=os.path.getmtime) if matches else None


_INVOICE_FNAME_RE = re.compile(r'^\d{4}-\d{2}-\d{2}_\d+\.pdf$')


def cleanup_old_invoices(output_dir: str = None, max_age_days: int = 60) -> int:
    """Delete generated invoice PDFs older than max_age_days.

    Only files whose names match the invoice pattern (YYYY-MM-DD_<id>.pdf)
    are touched; any other PDF that may exist in output/ is left alone.
    Returns number of files deleted.
    """
    root = output_dir or _DEFAULT_OUTPUT_ROOT
    if not os.path.isdir(root):
        return 0
    cutoff = time.time() - max_age_days * 86400
    count = 0
    for pdf_path in glob.glob(os.path.join(root, '**', '*.pdf'), recursive=True):
        if not _INVOICE_FNAME_RE.match(os.path.basename(pdf_path)):
            continue
        try:
            if os.path.getmtime(pdf_path) < cutoff:
                os.remove(pdf_path)
                count += 1
        except OSError:
            pass
    return count
