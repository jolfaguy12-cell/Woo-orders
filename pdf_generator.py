import os
from datetime import datetime
import jdatetime
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML
from dotenv import load_dotenv

load_dotenv()

FONT_PATH = os.getenv('FONT_PATH', '')
STORE_NAME = os.getenv('STORE_NAME', 'نام فروشگاه')
STORE_PHONE = os.getenv('STORE_PHONE', 'تلفن فروشگاه')
STORE_ADDRESS = os.getenv('STORE_ADDRESS', 'آدرس فروشگاه')
STORE_POSTCODE = os.getenv('STORE_POSTCODE', 'کد پستی فروشگاه')
SITE_URL = os.getenv('SITE_URL', 'https://yoursite.com').rstrip('/')

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')

A4_HEIGHT_MM = 297


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


def _content_height_mm(html_content: str) -> float:
    """Estimate rendered content height in mm using WeasyPrint layout."""
    doc = HTML(string=html_content, base_url=TEMPLATES_DIR).render()
    if len(doc.pages) > 1:
        return A4_HEIGHT_MM
    px_to_mm = 25.4 / 96.0
    max_bottom = 0.0
    for page in doc.pages:
        def traverse(box):
            nonlocal max_bottom
            btype = type(box).__name__
            tag = getattr(box, 'element_tag', '')
            if btype not in ('PageBox', 'MarginBox') and tag not in ('html', 'body'):
                if hasattr(box, 'position_y') and hasattr(box, 'height'):
                    if box.position_y is not None and box.height is not None:
                        max_bottom = max(max_bottom, box.position_y + box.height)
            if hasattr(box, 'children') and box.children:
                for child in box.children:
                    traverse(child)
        traverse(page._page_box)
    return max_bottom * px_to_mm


def generate_pdf(order: dict, output_dir: str = None, skip_packing_slip: bool = False) -> str:
    """
    Generate a PDF invoice (and optional packing slip) for the given order dict.
    Returns the path to the generated PDF file.
    """
    order = dict(order)  # avoid mutating caller's dict
    jalali_date = _parse_order_date(order.get('date_created', ''))
    order['jalali_date'] = jalali_date.strftime('%Y/%m/%d')

    total_items = sum(item.get('quantity', 0) for item in order.get('line_items', []))

    store = {
        'name': STORE_NAME,
        'phone': STORE_PHONE,
        'address': STORE_ADDRESS,
        'postcode': STORE_POSTCODE,
        'url': SITE_URL,
    }

    css_ctx = {'font_path': os.path.abspath(FONT_PATH) if FONT_PATH else ''}
    css = _render('style.css', css_ctx)

    invoice_html = _render('invoice.html', {'order': order, 'store': store})
    probe_html = f"<html><head><style>{css}</style></head><body>{invoice_html}</body></html>"
    force_page_break = _content_height_mm(probe_html) > (A4_HEIGHT_MM * 0.65)

    packing_html = ""
    shipping = order.get('shipping', {})
    if not skip_packing_slip and (shipping.get('first_name') or shipping.get('address_1')):
        packing_html = _render('packing_slip.html', {
            'order': order,
            'store': store,
            'total_items': total_items,
            'force_page_break': force_page_break,
        })

    final_html = f"<html><head><style>{css}</style></head><body>{invoice_html}{packing_html}</body></html>"

    year = jalali_date.strftime('%Y')
    month = jalali_date.strftime('%m')
    day = jalali_date.strftime('%d')
    order_id = order.get('id', 'UNKNOWN')

    if not output_dir:
        output_dir = os.path.join('output', year, month)
    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(output_dir, f"{year}-{month}-{day}_{order_id}.pdf")
    HTML(string=final_html, base_url=TEMPLATES_DIR).write_pdf(output_path)
    print(f"PDF generated: {output_path}")
    return output_path
