"""Celery compatibility shim: allows running with or without the Celery package."""
from __future__ import annotations

from typing import Any, Callable


class _LocalTask:
    def __init__(self, func: Callable[..., Any]) -> None:
        self.func = func
        self.__name__ = getattr(func, "__name__", "task")

    def delay(self, *args: Any, **kwargs: Any) -> Any:
        return None

    def apply_async(self, *args: Any, **kwargs: Any) -> Any:
        return None

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.func(*args, **kwargs)


class DummyCeleryApp:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.conf = type("Conf", (), {"timezone": "UTC", "beat_schedule": {}})()

    def task(self, func: Callable[..., Any]) -> Any:
        return _LocalTask(func)


try:
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
except ImportError:
    celery_app = DummyCeleryApp()
