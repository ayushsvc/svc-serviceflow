from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CategoryCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    parent_id: int | None = None


class CategoryRead(CategoryCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_id: int


class GroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    entity_id: int


class ActorCreate(BaseModel):
    actor_type: str = Field(pattern="^(USER|GROUP)$")
    actor_id: int = Field(gt=0)
    role: str = Field(pattern="^(REQUESTER|OBSERVER|ASSIGNEE)$")


class ActorRead(ActorCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ticket_id: int


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=5, max_length=240)
    description: str = Field(min_length=10, max_length=30000)
    type: str = Field(default="INCIDENT", pattern="^(INCIDENT|REQUEST|PROBLEM|PROJECT|FILE_SHARE|ADMIN_ACCESS|VPN_ACCESS)$")
    urgency: int = Field(default=3, ge=1, le=5)
    impact: int = Field(default=3, ge=1, le=5)
    category_id: int | None = None
    sla_id: int | None = Field(default=None, gt=0)
    requester_id: int | None = Field(default=None, gt=0)
    assignee_id: int | None = Field(default=None, gt=0)
    ticket_type_id: int | None = Field(default=None, gt=0)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class RequesterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str


class TicketUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=5, max_length=240)
    description: str | None = Field(default=None, min_length=10, max_length=30000)
    status: str | None = None
    urgency: int | None = Field(default=None, ge=1, le=5)
    impact: int | None = Field(default=None, ge=1, le=5)
    category_id: int | None = None
    assignee_id: int | None = None
    ticket_type_id: int | None = Field(default=None, gt=0)
    custom_fields: dict[str, Any] | None = None


class FollowupCreate(BaseModel):
    body: str = Field(min_length=1, max_length=30000)
    is_private: bool = False


class FollowupRead(FollowupCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ticket_id: int
    author_id: int
    created_at: datetime


class TaskCreate(BaseModel):
    body: str = Field(min_length=1, max_length=30000)
    assignee_id: int | None = None
    planned_start: datetime | None = None
    planned_end: datetime | None = None
    duration: int = Field(default=0, ge=0)
    state: str = Field(default="TODO", pattern="^(TODO|IN_PROGRESS|DONE|CANCELLED)$")


class TaskRead(TaskCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ticket_id: int
    created_at: datetime


class SolutionCreate(BaseModel):
    body: str = Field(min_length=1, max_length=30000)
    solution_type: str = Field(default="FIX", max_length=40)


class SolutionRead(SolutionCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ticket_id: int
    author_id: int
    status: str
    created_at: datetime


class LinkCreate(BaseModel):
    linked_ticket_id: int = Field(gt=0)
    link_type: str = Field(pattern="^(DUPLICATE|PARENT|CHILD|RELATED)$")


class LinkRead(LinkCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ticket_id: int
    created_at: datetime


class TicketRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    description: str
    type: str
    status: str
    urgency: int
    impact: int
    priority: int
    category_id: int | None
    entity_id: int
    source: str
    requester_id: int
    assignee_id: int | None
    opened_at: datetime
    solved_at: datetime | None
    closed_at: datetime | None
    tto_due_at: datetime | None
    ttr_due_at: datetime | None
    sla_id: int | None
    custom_fields: dict[str, Any]
    ticket_type_id: int | None
    requester_name: str | None = None
    requester_active: bool | None = None
    requester_vip: bool | None = None
    flagged: bool | None = None


class TicketListResponse(BaseModel):
    items: list[TicketRead]
    page: int
    page_size: int
    total: int
    pages: int


class ListProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern="^(filter|column)$")
    config: dict[str, Any]
    is_default: bool = False


class ListProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict[str, Any] | None = None


class ListProfileRead(ListProfileCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    entity_id: int
    created_at: datetime
    updated_at: datetime
