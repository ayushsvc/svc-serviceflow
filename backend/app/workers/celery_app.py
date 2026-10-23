from celery import Celery

from app.core.config import get_settings

celery_app = Celery("svc_service_desk", broker=get_settings().redis_url, backend=get_settings().redis_url, include=["app.workers.tasks"])
celery_app.conf.timezone = "UTC"
celery_app.conf.beat_schedule = {
    "check-sla-breaches": {
        "task": "app.workers.tasks.check_sla_breaches",
        "schedule": 60.0,
    },
    "create-recurring-tickets": {
        "task": "app.workers.tasks.create_recurring_tickets",
        "schedule": 60.0,
    },
}
