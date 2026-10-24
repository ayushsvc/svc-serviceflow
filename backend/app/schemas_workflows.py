from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TicketWorkflowWrite(BaseModel):
    states: list[str] = Field(min_length=1, max_length=40)
    transitions: dict[str, list[str]]
    approval_requirements: dict[str, int] = Field(default_factory=dict)
    is_active: bool = True

    @model_validator(mode="after")
    def validate_graph(self) -> "TicketWorkflowWrite":
        if any(not state or len(state) > 40 for state in self.states):
            raise ValueError("Workflow states must contain 1-40 characters")
        if len(self.states) != len(set(self.states)):
            raise ValueError("Workflow states must be unique")
        state_set = set(self.states)
        if set(self.transitions) - state_set:
            raise ValueError("Workflow transition sources must be configured states")
        for source, targets in self.transitions.items():
            if len(targets) != len(set(targets)) or set(targets) - state_set:
                raise ValueError(f"Workflow transition targets for {source} must be unique configured states")
        if set(self.approval_requirements) - state_set:
            raise ValueError("Approval requirements must refer to configured target states")
        if any(type(count) is not int or count < 0 or count > 20 for count in self.approval_requirements.values()):
            raise ValueError("Approval requirements must be between 0 and 20")
        return self


class TicketWorkflowRead(TicketWorkflowWrite):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_id: int
    record_type: str
    updated_at: datetime


class ApprovalCreate(BaseModel):
    record_type: str = Field(default="ticket", pattern="^ticket$")
    record_id: int = Field(gt=0)
    transition_to: str = Field(min_length=1, max_length=40)
    step: int = Field(default=1, ge=1, le=20)
    approver_user_id: int | None = Field(default=None, gt=0)
    approver_group_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def require_single_approver(self) -> "ApprovalCreate":
        if (self.approver_user_id is None) == (self.approver_group_id is None):
            raise ValueError("Specify exactly one user or group approver")
        return self


class ApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(APPROVED|REJECTED)$")
    note: str | None = Field(default=None, max_length=2000)


class ApprovalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_id: int
    record_type: str
    record_id: int
    transition_to: str
    step: int
    approver_user_id: int | None
    approver_group_id: int | None
    status: str
    note: str | None
    requested_by: int
    decided_by: int | None
    created_at: datetime
    decided_at: datetime | None
