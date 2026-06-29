"""
APScheduler wrapper for the daily Telegram report.

Usage (from run_dashboard.py):
    from dashboard.scheduler import start_scheduler
    start_scheduler(settings_loader_fn)

The scheduler reads the send_time from settings at startup and reschedules
itself when settings change.
"""

import atexit
import logging
from zoneinfo import ZoneInfo

_log = logging.getLogger(__name__)
_TZ = ZoneInfo('Asia/Tehran')

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    _APScheduler_available = True
except ImportError:
    _APScheduler_available = False
    _log.warning("APScheduler not installed; daily report will not be scheduled.")

_scheduler = None


def _parse_time(send_time: str):
    """Parse 'HH:MM' into (hour, minute). Returns (23, 55) on error."""
    try:
        h, m = send_time.strip().split(':')
        return int(h), int(m)
    except Exception:
        return 23, 55


def _make_cleanup_fn():
    """Return the daily invoice PDF cleanup job function (60-day retention)."""
    import os, sys

    def job():
        try:
            this_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(this_dir)
            sys.path.insert(0, project_root)
            from pdf_generator import cleanup_old_invoices
            deleted = cleanup_old_invoices(max_age_days=60)
            _log.info("Invoice cleanup: %d old PDF(s) deleted.", deleted)
        except Exception as exc:
            _log.error("Invoice cleanup job error: %s", exc)

    return job


def _make_job_fn():
    """Return the daily report job function (reads settings at runtime)."""
    import os, sys

    def job():
        # Import here so we get the live module state, not a captured reference
        try:
            # Resolve project root dynamically
            this_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(this_dir)

            sys.path.insert(0, this_dir)
            sys.path.insert(0, project_root)

            from settings_manager import load as load_settings
            from daily_report import send_daily_report
            from dotenv import dotenv_values

            cfg = load_settings()
            dr_cfg = cfg.get('daily_report', {})
            if not dr_cfg.get('enabled', True):
                _log.info("daily_report job: disabled in settings, skipping.")
                return

            env = dotenv_values(os.path.join(project_root, '.env'))
            bot_token = env.get('TG_BOT_TOKEN', '')
            destinations_file = os.path.join(project_root, 'telegram_destinations.json')

            result = send_daily_report(
                hub_url=cfg['hub_url'],
                hub_key=cfg['hub_api_key'],
                bot_token=bot_token,
                destinations_file=destinations_file,
            )
            _log.info("daily_report job result: %s", result)
        except Exception as exc:
            _log.error("daily_report job error: %s", exc, exc_info=True)

    return job


def start_scheduler(send_time: str = '23:55'):
    """Start the APScheduler background scheduler with the daily report job."""
    global _scheduler

    if not _APScheduler_available:
        return

    if _scheduler and _scheduler.running:
        _log.info("Scheduler already running; skipping start.")
        return

    _scheduler = BackgroundScheduler(timezone=_TZ)
    _scheduler.start()
    atexit.register(lambda: _scheduler.shutdown(wait=False))

    h, m = _parse_time(send_time)
    _scheduler.add_job(
        _make_job_fn(),
        CronTrigger(hour=h, minute=m, timezone=_TZ),
        id='daily_report',
        replace_existing=True,
        misfire_grace_time=300,
    )
    _scheduler.add_job(
        _make_cleanup_fn(),
        CronTrigger(hour=3, minute=0, timezone=_TZ),
        id='invoice_cleanup',
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _log.info("Scheduler started; daily report at %02d:%02d Tehran time.", h, m)


def reschedule(send_time: str):
    """Update the daily report job's fire time (after settings change)."""
    global _scheduler
    if not _APScheduler_available or not _scheduler or not _scheduler.running:
        return
    h, m = _parse_time(send_time)
    try:
        _scheduler.reschedule_job(
            'daily_report',
            trigger=CronTrigger(hour=h, minute=m, timezone=_TZ),
        )
        _log.info("Rescheduled daily report to %02d:%02d Tehran time.", h, m)
    except Exception as exc:
        _log.error("reschedule failed: %s", exc)


def get_next_fire_time() -> str | None:
    """Return the next fire time as ISO string, or None if not scheduled."""
    if not _APScheduler_available or not _scheduler or not _scheduler.running:
        return None
    jobs = _scheduler.get_jobs()
    for j in jobs:
        if j.id == 'daily_report' and j.next_run_time:
            return j.next_run_time.isoformat()
    return None
