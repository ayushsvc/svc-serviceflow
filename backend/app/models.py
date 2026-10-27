from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ProfileInterface(StrEnum):
    SELF_SERVICE = "self_service"
    HELPDESK = "helpdesk"


class Entity(Base):
    __tablename__ = "entities"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"), nullable=True)
    users: Mapped[list["User"]] = relationship(back_populates="entity")


class Profile(Base):
    __tablename__ = "profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    interface: Mapped[str] = mapped_column(String(30), default=ProfileInterface.SELF_SERVICE.value)
    rights: Mapped[dict] = mapped_column(JSONB, default=dict)
    users: Mapped[list["User"]] = relationship(back_populates="profile")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(160))
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id"))
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    profile: Mapped[Profile] = relationship(back_populates="users")
    entity: Mapped[Entity] = relationship(back_populates="users")


class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)


class GroupMember(Base):
    __tablename__ = "group_members"
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (CheckConstraint("level BETWEEN 1 AND 4", name="ck_categories_level"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    level: Mapped[int] = mapped_column(Integer, default=1)


class CategoryTeam(Base):
    __tablename__ = "category_teams"
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)


class FieldDefinition(Base):
    __tablename__ = "field_definitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(160))
    type: Mapped[str] = mapped_column(String(20))
    options: Mapped[dict] = mapped_column(JSONB, default=dict)
    validation: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class TicketTypeDefinition(Base):
    __tablename__ = "ticket_types"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    itil_class: Mapped[str] = mapped_column(String(20))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"), nullable=True)
    default_team_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"), nullable=True)
    default_priority: Mapped[int] = mapped_column(Integer, default=3)
    default_sla_id: Mapped[int | None] = mapped_column(ForeignKey("slas.id"), nullable=True)
    icon: Mapped[str | None] = mapped_column(String(40), nullable=True)
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"), nullable=True, index=True)


class TicketTypeField(Base):
    __tablename__ = "ticket_type_fields"
    ticket_type_id: Mapped[int] = mapped_column(ForeignKey("ticket_types.id", ondelete="CASCADE"), primary_key=True)
    field_definition_id: Mapped[int] = mapped_column(ForeignKey("field_definitions.id", ondelete="CASCADE"), primary_key=True)
    order: Mapped[int] = mapped_column(Integer, default=0)
    page: Mapped[str] = mapped_column(String(40), default="details")
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    visible_to_roles: Mapped[list] = mapped_column(JSONB, default=list)
    editable_by_roles: Mapped[list] = mapped_column(JSONB, default=list)
    default_value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    show_on_portal: Mapped[bool] = mapped_column(Boolean, default=True)
    show_condition: Mapped[dict] = mapped_column(JSONB, default=dict)


class TicketFieldValue(Base):
    __tablename__ = "ticket_field_values"
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True)
    field_definition_id: Mapped[int] = mapped_column(ForeignKey("field_definitions.id", ondelete="CASCADE"), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)


class TicketTemplate(Base):
    __tablename__ = "ticket_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    ticket_type_id: Mapped[int] = mapped_column(ForeignKey("ticket_types.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    show_on_portal: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    prefilled: Mapped[dict] = mapped_column(JSONB, default=dict)


class TicketTemplateUsage(Base):
    __tablename__ = "ticket_template_usage"
    __table_args__ = (
        UniqueConstraint("entity_id", "user_id", "template_id", name="uq_ticket_template_usage_owner"),
        Index("ix_ticket_template_usage_recent", "entity_id", "user_id", "last_used_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("ticket_templates.id", ondelete="CASCADE"), index=True)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    uses: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class TicketType(StrEnum):
    INCIDENT = "INCIDENT"
    REQUEST = "REQUEST"


class ListProfile(Base):
    __tablename__ = "list_profiles"
    __table_args__ = (
        CheckConstraint("kind IN ('filter', 'column')", name="ck_list_profiles_kind"),
        UniqueConstraint("user_id", "kind", "name", name="uq_list_profiles_user_kind_name"),
        Index("ix_list_profiles_owner_kind_name", "user_id", "entity_id", "kind", "name"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TicketWorkflow(Base):
    __tablename__ = "ticket_workflows"
    __table_args__ = (UniqueConstraint("entity_id", "record_type", name="uq_ticket_workflows_entity_type"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    record_type: Mapped[str] = mapped_column(String(40))
    states: Mapped[list] = mapped_column(JSONB, default=list)
    transitions: Mapped[dict] = mapped_column(JSONB, default=dict)
    approval_requirements: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WorkflowApproval(Base):
    __tablename__ = "workflow_approvals"
    __table_args__ = (
        CheckConstraint(
            "(approver_user_id IS NOT NULL AND approver_group_id IS NULL) OR "
            "(approver_user_id IS NULL AND approver_group_id IS NOT NULL)",
            name="ck_workflow_approvals_single_approver",
        ),
        CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED')", name="ck_workflow_approvals_status"),
        Index("ix_workflow_approvals_record_status", "entity_id", "record_type", "record_id", "transition_to", "status"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    record_type: Mapped[str] = mapped_column(String(40))
    record_id: Mapped[int] = mapped_column(Integer)
    transition_to: Mapped[str] = mapped_column(String(40))
    step: Mapped[int] = mapped_column(Integer, default=1)
    approver_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    approver_group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TicketStatus(StrEnum):
    NEW = "NEW"
    ASSIGNED = "ASSIGNED"
    PLANNED = "PLANNED"
    PENDING = "PENDING"
    SOLVED = "SOLVED"
    CLOSED = "CLOSED"


class ActorType(StrEnum):
    USER = "USER"
    GROUP = "GROUP"


class ActorRole(StrEnum):
    REQUESTER = "REQUESTER"
    OBSERVER = "OBSERVER"
    ASSIGNEE = "ASSIGNEE"


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(20), default=TicketType.INCIDENT.value)
    status: Mapped[str] = mapped_column(String(20), default=TicketStatus.NEW.value, index=True)
    urgency: Mapped[int] = mapped_column(Integer, default=3)
    impact: Mapped[int] = mapped_column(Integer, default=3)
    priority: Mapped[int] = mapped_column(Integer, default=3, index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    source: Mapped[str] = mapped_column(String(30), default="web")
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    solved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tto_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    ttr_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    sla_id: Mapped[int | None] = mapped_column(ForeignKey("slas.id"), nullable=True)
    action_time_seconds: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[int] = mapped_column(Integer, default=0)
    custom_fields: Mapped[dict] = mapped_column(JSONB, default=dict)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    ticket_type_id: Mapped[int | None] = mapped_column(ForeignKey("ticket_types.id"), nullable=True, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    category: Mapped[Category | None] = relationship()
    requester: Mapped[User] = relationship(foreign_keys=[requester_id])
    assignee: Mapped[User | None] = relationship(foreign_keys=[assignee_id])
    actors: Mapped[list["TicketActor"]] = relationship(cascade="all, delete-orphan")
    followups: Mapped[list["TicketFollowup"]] = relationship(cascade="all, delete-orphan")


class TicketActor(Base):
    __tablename__ = "ticket_actors"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    actor_type: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))


class TicketFollowup(Base):
    __tablename__ = "ticket_followups"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TicketTask(Base):
    __tablename__ = "ticket_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    body: Mapped[str] = mapped_column(Text)
    planned_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    planned_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(20), default="TODO")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TicketSolution(Base):
    __tablename__ = "ticket_solutions"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    solution_type: Mapped[str] = mapped_column(String(40), default="FIX")
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="PROPOSED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TicketDocument(Base):
    __tablename__ = "ticket_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(500))
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class TicketLink(Base):
    __tablename__ = "ticket_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    linked_ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"))
    link_type: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SLA(Base):
    __tablename__ = "slas"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    tto_minutes: Mapped[int] = mapped_column(Integer, default=60)
    ttr_minutes: Mapped[int] = mapped_column(Integer, default=480)
    calendar: Mapped[dict] = mapped_column(JSONB, default=dict)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PriorityMatrix(Base):
    __tablename__ = "priority_matrices"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), unique=True, index=True)
    matrix: Mapped[list] = mapped_column(JSONB, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class SLAEscalation(Base):
    __tablename__ = "sla_escalations"
    id: Mapped[int] = mapped_column(primary_key=True)
    sla_id: Mapped[int] = mapped_column(ForeignKey("slas.id", ondelete="CASCADE"), index=True)
    trigger_pct: Mapped[int] = mapped_column(Integer)
    action: Mapped[dict] = mapped_column(JSONB, default=dict)


class BusinessRule(Base):
    __tablename__ = "business_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    trigger: Mapped[str] = mapped_column(String(30))
    conditions: Mapped[dict] = mapped_column(JSONB, default=dict)
    actions: Mapped[dict] = mapped_column(JSONB, default=dict)
    order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)


class NotificationTemplate(Base):
    __tablename__ = "notification_templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    event: Mapped[str] = mapped_column(String(60), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    body_jinja: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)


class RecurringTicket(Base):
    __tablename__ = "recurring_tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    cron_expr: Mapped[str] = mapped_column(String(100))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    urgency: Mapped[int] = mapped_column(Integer, default=3)
    impact: Mapped[int] = mapped_column(Integer, default=3)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    entity: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    diff: Mapped[dict] = mapped_column(JSONB, default=dict)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
