"""Read/write dashboard settings from a JSON file under dashboard/data/."""

import json
import os

_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), 'data', 'settings.json')

_DEFAULTS = {
    "hub_url": "http://127.0.0.1:8090",
    "hub_api_key": "8dc7ba99612f201c2bc1b2146a1bc26148df0f2ddd7de6ba6a4a74149e34470d",
    "invoice_api_key": "",
    "target_order_statuses": "processing,ready-to-ship,bslm-preparation,bslm-shipping,bslm-wait-vendor,bslm-rejected,refunded",
    "basalam_only": True,
    "templates": {
        "new_order": (
            "📦 *سفارش #{order_id}*\n"
            "📌 وضعیت: {status_label}\n"
            "👤 مشتری: {customer_name}\n"
            "📞 تلفن: {phone}\n"
            "💰 مبلغ کل: {total} تومان\n"
            "💳 پرداخت: {payment_method}\n"
            "📅 تاریخ: {order_date}\n\n"
            "🛒 محصولات:\n{items_list}"
        ),
        "basalam_order": (
            "🛍 *باسلام — سفارش #{order_id}*\n"
            "📌 وضعیت: {status_label}\n"
            "👤 مشتری: {customer_name}\n"
            "📞 تلفن: {phone}\n"
            "💰 مبلغ کل: {total} تومان\n"
            "💼 کارمزد باسلام: {basalam_fee} تومان\n"
            "💵 مبلغ دریافتی: {basalam_net} تومان\n"
            "🔢 تعداد خرید مشتری: {basalam_purchase_count}\n\n"
            "🛒 محصولات:\n{items_list}"
        ),
        "low_stock": (
            "⚠️ *هشدار موجودی کم*\n"
            "📦 محصول: {product_name}\n"
            "🔢 موجودی فعلی: {stock_quantity} عدد\n"
            "🆔 SKU: {sku}\n"
            "🏷️ شناسه: {product_id}"
        ),
        "out_of_stock": (
            "🚫 *اتمام موجودی*\n"
            "📦 محصول: {product_name}\n"
            "🆔 SKU: {sku}\n"
            "🏷️ شناسه: {product_id}"
        ),
        "tracking": (
            "📮 *کد رهگیری مرسوله*\n"
            "📦 سفارش #{order_id}\n"
            "👤 مشتری: {customer_name}\n"
            "🔑 کد رهگیری: {tracking_code}\n"
            "🔗 لینک رهگیری: {tracking_url}"
        ),
        "daily_report": (
            "📊 <b>گزارش روزانه فروش — {date}</b>\n"
            + "─" * 28 + "\n"
            + "\n"
            + "📦 <b>سفارشات</b>\n"
            + "• کل سفارشات: <b>{total_orders}</b>\n"
            + "• سفارشات سایت: {website_orders}\n"
            + "• سفارشات باسلام: {basalam_orders}\n"
            + "• لغو شده: {cancelled_orders}\n"
            + "• ارسال رایگان (مجموع ≥۲م): {free_ship_orders}\n"
            + "\n"
            + "💰 <b>فروش</b>\n"
            + "• جمع فروش: <b>{daily_sales}</b>\n"
            + "• بدون هزینه ارسال: {sales_excl_shipping}\n"
            + "• خالص (بدون کمیسیون و ارسال): {daily_sales_net}\n"
            + "• هزینه ارسال: {total_shipping}\n"
            + "\n"
            + "🛍 <b>باسلام</b>\n"
            + "• جمع فروش باسلام: {basalam_total_sales}\n"
            + "• خالص بعد از کمیسیون: {basalam_net_sales}\n"
            + "\n"
            + "📮 <b>ارسال و شهر</b>\n"
            + "• بسته‌های ارسال شده امروز: {packages_shipped}\n"
            + "• سفارشات قم: {qom_orders}\n"
            + "• سفارشات سایر شهرها: {non_qom_orders}"
        ),
    },
    "stock_alerts": {
        "low_stock_enabled": True,
        "low_stock_threshold": 5,
        "out_of_stock_enabled": True,
    },
    "daily_report": {
        "enabled": True,
        "send_time": "23:55",
    },
    "send_pdf_with_new_order": True,
    "telegram_manager_ids": [],
}


def load() -> dict:
    if os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Deep-merge with defaults so new keys are always present
            merged = dict(_DEFAULTS)
            merged.update(data)
            if 'templates' in data:
                merged['templates'] = dict(_DEFAULTS['templates'])
                merged['templates'].update(data['templates'])
            if 'stock_alerts' in data:
                merged['stock_alerts'] = dict(_DEFAULTS['stock_alerts'])
                merged['stock_alerts'].update(data['stock_alerts'])
            if 'daily_report' in data:
                merged['daily_report'] = dict(_DEFAULTS['daily_report'])
                merged['daily_report'].update(data['daily_report'])
            return merged
        except Exception:
            pass
    return dict(_DEFAULTS)


def save(settings: dict):
    os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
    with open(_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
