from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from io import BytesIO

from app.api import tickets
from app.deps import require_admin, require_helpdesk
from app.schemas_tickets import ActorCreate, FollowupCreate, LinkCreate, SolutionCreate, TaskCreate, TicketUpdate


class EmptyResult:
    def all(self) -> list[object]:
        return []


class EmptyDb:
    async def scalar(self, _query: object) -> None:
        return None

    async def scalars(self, _query: object) -> EmptyResult:
        return EmptyResult()


def user(role: str, interface: str) -> SimpleNamespace:
    return SimpleNamespace(id=99, entity_id=999, profile=SimpleNamespace(name=role, interface=interface))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        lambda u, db: tickets.get_ticket(1, u, db),
        lambda u, db: tickets.update_ticket(1, TicketUpdate(title="Changed title"), u, db),
        lambda u, db: tickets.delete_ticket(1, u, db),
        lambda u, db: tickets.actors(1, u, db),
        lambda u, db: tickets.add_actor(1, ActorCreate(actor_type="USER", actor_id=1, role="OBSERVER"), u, db),
        lambda u, db: tickets.followups(1, u, db),
        lambda u, db: tickets.add_followup(1, FollowupCreate(body="Reply"), u, db),
        lambda u, db: tickets.list_tasks(1, u, db),
        lambda u, db: tickets.create_task(1, TaskCreate(body="Task"), u, db),
        lambda u, db: tickets.list_solutions(1, u, db),
        lambda u, db: tickets.propose_solution(1, SolutionCreate(body="Solution"), u, db),
        lambda u, db: tickets.approve_solution(1, 1, u, db),
        lambda u, db: tickets.upload_document(1, u, db, UploadFile(BytesIO(b"%PDF-1.7"))),
        lambda u, db: tickets.download_document(1, 1, u, db),
        lambda u, db: tickets.list_links(1, u, db),
        lambda u, db: tickets.link_ticket(1, LinkCreate(linked_ticket_id=2, link_type="RELATED"), u, db),
        lambda u, db: tickets.timeline(1, u, db),
    ],
)
async def test_ticket_subresources_reject_unknown_entity(operation: object) -> None:
    with pytest.raises(HTTPException) as error:
        await operation(user("Administrator", "helpdesk"), EmptyDb())  # type: ignore[operator]
    assert error.value.status_code == 404


def test_role_matrix_for_helpdesk_and_administrator_guards() -> None:
    administrator = user("Administrator", "helpdesk")
    technician = user("Technician", "helpdesk")
    requester = user("Requester", "self_service")

    assert require_helpdesk(administrator) is administrator
    assert require_helpdesk(technician) is technician
    assert require_admin(administrator) is administrator
    with pytest.raises(HTTPException) as helpdesk_error:
        require_helpdesk(requester)
    assert helpdesk_error.value.status_code == 403
    with pytest.raises(HTTPException) as admin_error:
        require_admin(technician)
    assert admin_error.value.status_code == 403
