from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SLACreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    tto_minutes: int = Field(default=60, ge=1, le=10080)
    ttr_minutes: int = Field(default=480, ge=1, le=43200)
    calendar: dict = Field(default_factory=dict)


class SLARead(SLACreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_id: int
    is_active: bool


class RuleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    trigger: str = Field(pattern="^(ON_CREATE|ON_UPDATE|ON_EMAIL)$")
    conditions: dict = Field(default_factory=dict)
    actions: dict = Field(default_factory=dict)
    order: int = Field(default=0, ge=0)


class RuleRead(RuleCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_id: int
    is_active: bool


class NotificationTemplateCreate(BaseModel):
    event: str = Field(min_length=2, max_length=60)
    subject: str = Field(min_length=2, max_length=255)
    body_jinja: str = Field(min_length=1, max_length=30000)


class NotificationTemplateRead(NotificationTemplateCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_id: int


class RecurringTicketCreate(BaseModel):
    title: str = Field(min_length=5, max_length=240)
    description: str = Field(min_length=10, max_length=30000)
    cron_expr: str = Field(min_length=5, max_length=100)
    next_run_at: datetime
    requester_id: int
    urgency: int = Field(default=3, ge=1, le=5)
    impact: int = Field(default=3, ge=1, le=5)


class RecurringTicketRead(RecurringTicketCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_id: int
    is_active: bool
