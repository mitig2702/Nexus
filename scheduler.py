import os
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.background import BackgroundScheduler

from tools import check_sla_breaches
from db import execute, rows_to_dicts
from metrics import get_roi_stats, record_digest_sent
from integrations import post_to_slack, SLACK_DIGEST_CHANNEL

logger = logging.getLogger("nexus.scheduler")

_scheduler: BackgroundScheduler | None = None

SLA_SWEEP_MINUTES = int(os.getenv("SLA_SWEEP_MINUTES", "30"))
DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "9"))


def _sla_sweep_job():
    escalated = check_sla_breaches()
    if escalated and not (len(escalated) == 1 and "error" in escalated[0]):
        logger.info(f"SLA sweep escalated {len(escalated)} ticket(s)")
        if SLACK_DIGEST_CHANNEL:
            lines = "\n".join(f"- {e['ticket_id']}: {e['title']}" for e in escalated)
            post_to_slack(SLACK_DIGEST_CHANNEL, f":rotating_light: SLA breach — auto-escalated {len(escalated)} ticket(s) to high:\n{lines}")


def _daily_digest_job():
    open_tickets = rows_to_dicts(execute(
        "SELECT priority, COUNT(*) as count FROM tickets WHERE status = 'open' GROUP BY priority"
    ))
    roi = get_roi_stats()
    lines = [
        f":sunrise: *Nexus daily ops digest* — {datetime.now(timezone.utc).date().isoformat()}",
        f"Open tickets: {sum(int(t['count']) for t in open_tickets)} " + ", ".join(f"{t['priority']}: {t['count']}" for t in open_tickets),
        f"Tickets auto-resolved by KB: {roi['tickets_auto_resolved_by_kb']} (deflection rate {roi['deflection_rate']*100:.0f}%)",
        f"Estimated time saved: {roi['estimated_minutes_saved']} min",
    ]
    record_digest_sent()
    if SLACK_DIGEST_CHANNEL:
        post_to_slack(SLACK_DIGEST_CHANNEL, "\n".join(lines))
    else:
        logger.info("Daily digest (Slack not configured): " + " | ".join(lines))


def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    if os.getenv("NEXUS_ENABLE_SCHEDULER", "true").lower() == "false":
        logger.info("Scheduler disabled via NEXUS_ENABLE_SCHEDULER=false")
        return None

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(_sla_sweep_job, "interval", minutes=SLA_SWEEP_MINUTES, id="sla_sweep")
    _scheduler.add_job(_daily_digest_job, "cron", hour=DIGEST_HOUR, id="daily_digest")
    _scheduler.start()
    logger.info(f"Scheduler started: SLA sweep every {SLA_SWEEP_MINUTES}min, daily digest at {DIGEST_HOUR}:00 UTC")
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
