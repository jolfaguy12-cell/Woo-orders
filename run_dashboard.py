#!/usr/bin/env python3
"""
Startup script for the Behdashtik Orders Dashboard.
Runs on port 8000; proxied by nginx at /orders-api.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'dashboard'))
os.chdir(os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

import auth as _auth
_auth.init_auth()

from app import app

# Start the daily-report scheduler
try:
    import settings_manager as _sm
    from scheduler import start_scheduler
    _cfg = _sm.load()
    _send_time = _cfg.get('daily_report', {}).get('send_time', '23:55')
    start_scheduler(_send_time)
except Exception as _e:
    import logging
    logging.getLogger(__name__).warning("Could not start scheduler: %s", _e)

# Start Telegram bot polling for admin search/management
try:
    from telegram_notify import start_bot_polling
    start_bot_polling()
except Exception as _e:
    import logging as _logging
    _logging.getLogger(__name__).warning("Could not start Telegram bot polling: %s", _e)

if __name__ == '__main__':
    port = int(os.getenv('DASHBOARD_PORT', '8000'))
    print(f"Dashboard running on http://0.0.0.0:{port}/orders-api")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
