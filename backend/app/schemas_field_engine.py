from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FieldDefinitionCreate(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=160)
    type: str = Field(pattern="^(text|textarea|richtext|number|date|datetime|select|multiselect|checkbox|user|asset|team|agent|category|url)$")
    options: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    is_system: bool = False


class FieldDefinitionRead(FieldDefinitionCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class TicketTypeFieldInput(BaseModel):
    field_definition_id: int = Field(gt=0)
    order: int = Field(default=0, ge=0)
    page: str = Field(default="details", min_length=1, max_length=40)
    required: bool = False
    visible_to_roles: list[str] = Field(default_factory=lambda: ["Administrator", "Technician", "Requester"])
    editable_by_roles: list[str] = Field(default_factory=lambda: ["Administrator", "Technician"])
    default_value: Any = None
    show_on_portal: bool = True
    show_condition: dict[str, Any] = Field(default_factory=dict)


class TicketTypeCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    itil_class: str = Field(pattern="^(INCIDENT|REQUEST|PROBLEM|CHANGE)$")
    group_id: int | None = Field(default=None, gt=0)
    default_team_id: int | None = Field(default=None, gt=0)
    default_priority: int = Field(default=3, ge=1, le=5)
    default_sla_id: int | None = Field(default=None, gt=0)
    icon: str | None = Field(default=None, max_length=40)
    color: str | None = Field(default=None, max_length=20)
    is_active: bool = True
    fields: list[TicketTypeFieldInput] = Field(default_factory=list)


class TicketTypeRead(TicketTypeCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_id: int | None


class TemplateCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    ticket_type_id: int = Field(gt=0)
    team_id: int | None = Field(default=None, gt=0)
    prefilled: dict[str, Any] = Field(default_factory=dict)


class TemplateRead(TemplateCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int


class FormField(BaseModel):
    key: str
    label: str
    type: str
    options: dict[str, Any]
    validation: dict[str, Any]
    required: bool
    editable: bool
    default_value: Any = None
    page: str
    show_on_portal: bool
    show_condition: dict[str, Any]


class FormSchemaRead(BaseModel):
    ticket_type_id: int
    name: str
    pages: dict[str, list[FormField]]


class PriorityMatrixPayload(BaseModel):
    matrix: list[list[int]]


class PriorityMatrixRead(PriorityMatrixPayload):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_id: int


class SlaPreview(BaseModel):
    sla_id: int | None
    sla_name: str | None
    response_target: str | None
    fix_target: str | None


class AssignmentPreview(BaseModel):
    team_id: int | None
    team_name: str | None
    agent_id: int | None
    agent_name: str | None
    priority: int
    sla: SlaPreview
