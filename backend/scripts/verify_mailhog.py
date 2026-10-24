import asyncio
import json
import os
from uuid import uuid4

import httpx
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Entity, TicketTypeDefinition, User

API_URL = os.getenv("API_BASE_URL", "http://api:8000").rstrip("/")
MAILHOG_URL = os.getenv("MAILHOG_API_URL", "http://mailhog:8025/api/v2/messages").rstrip("/")
REQUESTER_EMAIL = "priya@svcinfosys.in"


async def verify_mailhog_delivery() -> None:
    password = os.environ.get("SEED_REQUESTER_PASSWORD")
    if not password:
        raise RuntimeError("SEED_REQUESTER_PASSWORD must be available in the API container")

    async with SessionLocal() as db:
        entity = await db.scalar(select(Entity).where(Entity.name == "SVC Infosystems"))
        requester = await db.scalar(
            select(User).where(User.email == REQUESTER_EMAIL, User.entity_id == entity.id)
        ) if entity else None
        ticket_type = await db.scalar(
            select(TicketTypeDefinition).where(
                TicketTypeDefinition.itil_class == "INCIDENT",
                TicketTypeDefinition.is_active.is_(True),
                (TicketTypeDefinition.entity_id == entity.id) | (TicketTypeDefinition.entity_id.is_(None)),
            ).order_by(TicketTypeDefinition.entity_id.desc().nulls_last())
        ) if entity else None
    if entity is None or requester is None or ticket_type is None:
        raise RuntimeError("Seeded entity, requester, or incident type is missing")

    title = f"MailHog verification {uuid4().hex}"
    async with httpx.AsyncClient(timeout=10) as client:
        login = await client.post(
            f"{API_URL}/api/v1/auth/login",
            json={"email": REQUESTER_EMAIL, "password": password},
        )
        if login.status_code != 200:
            raise RuntimeError(f"Requester login failed with HTTP {login.status_code}")
        access_token = login.json()["access_token"]
        created = await client.post(
            f"{API_URL}/api/v1/tickets",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "title": title,
                "description": "Automated verification that notification delivery reaches MailHog.",
                "type": "INCIDENT",
                "ticket_type_id": ticket_type.id,
                "custom_fields": {
                    "summary": title,
                    "details": "MailHog message receipt verification.",
                },
            },
        )
        if created.status_code != 201:
            raise RuntimeError(f"Ticket creation failed with HTTP {created.status_code}")
        ticket_id = created.json()["id"]

        for _ in range(30):
            messages = await client.get(MAILHOG_URL, params={"limit": 100})
            if messages.status_code != 200:
                raise RuntimeError(f"MailHog returned HTTP {messages.status_code}")
            mailbox = json.dumps(messages.json()).lower()
            if REQUESTER_EMAIL.lower() in mailbox and str(ticket_id).lower() in mailbox:
                print(f"MailHog received the created-ticket notification for ticket #{ticket_id}.")
                return
            await asyncio.sleep(1)
    raise AssertionError(f"MailHog did not receive the created-ticket notification for ticket #{ticket_id}")


if __name__ == "__main__":
    asyncio.run(verify_mailhog_delivery())
