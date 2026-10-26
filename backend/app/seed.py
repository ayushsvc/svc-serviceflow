import asyncio
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import (
    BusinessRule,
    Category,
    Entity,
    FieldDefinition,
    Group,
    GroupMember,
    NotificationTemplate,
    Profile,
    ProfileInterface,
    SLA,
    TicketTypeDefinition,
    TicketTypeField,
    TicketTemplate,
    User,
)

SLA_TARGETS = {
    1: (15, 240),
    2: (30, 480),
    3: (60, 1440),
    4: (240, 2880),
    5: (480, 5760),
}
BUSINESS_HOURS = {
    "timezone": "Asia/Kolkata",
    "working_days": [1, 2, 3, 4, 5],
    "start": "09:00",
    "end": "17:00",
}
NOTIFICATION_TEMPLATES = [
    ("created", "Ticket {{ ticket.id }} created: {{ ticket.title }}", "Hello {{ user.name }}, ticket {{ ticket.id }} has been created."),
    ("assigned", "Ticket {{ ticket.id }} assigned", "Ticket {{ ticket.id }} has been assigned to the service team."),
    ("public_reply", "New reply on ticket {{ ticket.id }}", "There is a new public reply on ticket {{ ticket.id }}."),
    ("solved", "Ticket {{ ticket.id }} solved", "Ticket {{ ticket.id }} has been solved."),
    ("sla_breach", "SLA target breached for ticket {{ ticket.id }}", "An SLA target has been breached for ticket {{ ticket.id }}."),
]
ALL_ROLES = ["Administrator", "Technician", "Requester"]
HELPDESK_ROLES = ["Administrator", "Technician"]
PORTAL_EDITABLE_FIELDS = {"summary", "details", "category", "urgency", "impact"}
SERVER_DERIVED_FIELDS = {"user", "organisation"}
ADMIN_TECHNICIAN_FIELDS = {
    "priority", "team", "agent", "source", "asset", "sla", "due_date", "start_date",
    "followup_date", "time_estimate", "tags", "cc", "parent_ticket", "related_tickets", "flag",
}
ORIGINAL_REQUESTER_EDITABLE_FIELDS = {"summary", "details", "user", "organisation", "category"}
DEFAULT_TICKET_TEMPLATES = {
    "INCIDENT": [
        ("Cannot log in / password reset", "Get help restoring account access or resetting a password."),
        ("Email not working", "Report email delivery, access, or synchronization problems."),
        ("Laptop slow or not starting", "Request help diagnosing a slow or non-starting laptop."),
        ("Printer not working", "Report printer connectivity, queue, or print-quality problems."),
        ("Wi-Fi / network issue", "Report a Wi-Fi, wired network, or connectivity issue."),
        ("Software crashes or errors", "Report an application crash or unexpected error."),
    ],
    "REQUEST": [
        ("New joiner setup", "Prepare accounts, equipment, and access for a new colleague."),
        ("Software installation request", "Request installation of approved software."),
        ("Access / permission request", "Request access to a system, folder, or service."),
        ("Hardware request", "Request a device or other approved hardware."),
        ("VPN access", "Request secure remote access through the company VPN."),
    ],
    "PROBLEM": [
        ("Recurring issue investigation", "Investigate a recurring incident and identify its underlying cause."),
    ],
    "CHANGE": [
        ("Standard change", "Plan a routine, low-risk, repeatable service change."),
        ("Emergency change", "Record an urgent change needed to restore or protect service."),
    ],
}


async def seed_ticket_templates(db: AsyncSession, entity: Entity) -> list[str]:
    created: list[str] = []
    category_id = await db.scalar(
        select(Category.id)
        .where(Category.entity_id == entity.id)
        .order_by(Category.id)
        .limit(1)
    )
    for itil_class, defaults in DEFAULT_TICKET_TEMPLATES.items():
        ticket_type = await db.scalar(
            select(TicketTypeDefinition)
            .where(
                TicketTypeDefinition.itil_class == itil_class,
                TicketTypeDefinition.is_active.is_(True),
                (TicketTypeDefinition.entity_id == entity.id)
                | TicketTypeDefinition.entity_id.is_(None),
            )
            .order_by(TicketTypeDefinition.entity_id.is_(None), TicketTypeDefinition.id)
            .limit(1)
        )
        if ticket_type is None:
            continue
        field_rows = (
            await db.execute(
                select(FieldDefinition.key, TicketTypeField.field_definition_id)
                .join(
                    TicketTypeField,
                    TicketTypeField.field_definition_id == FieldDefinition.id,
                )
                .where(TicketTypeField.ticket_type_id == ticket_type.id)
            )
        ).all()
        available_fields = {key for key, _ in field_rows}
        for name, description in defaults:
            existing = await db.scalar(
                select(TicketTemplate).where(
                    TicketTemplate.entity_id == entity.id,
                    TicketTemplate.name == name,
                    TicketTemplate.ticket_type_id == ticket_type.id,
                )
            )
            if existing is not None:
                continue
            prefilled = {
                key: value
                for key, value in {
                    "summary": name,
                    "details": (
                        "<p>What happened?</p><p>When did it start?</p>"
                        "<p>What is the impact?</p>"
                    ),
                    "category": category_id,
                    "urgency": 3,
                    "impact": 3,
                }.items()
                if key in available_fields and (key != "category" or category_id is not None)
            }
            db.add(
                TicketTemplate(
                    entity_id=entity.id,
                    name=name,
                    description=description,
                    ticket_type_id=ticket_type.id,
                    prefilled=prefilled,
                    is_active=True,
                    show_on_portal=itil_class in {"INCIDENT", "REQUEST"},
                    sort_order=len(created),
                )
            )
            created.append(f"{ticket_type.name}: {name}")
    return created


async def seed_entity_defaults(db: AsyncSession, entity: Entity) -> list[str]:
    created_templates = await seed_ticket_templates(db, entity)
    team_name = f"{entity.name} Service Desk"
    team = await db.scalar(select(Group).where(Group.entity_id == entity.id, Group.name == team_name))
    if team is None:
        team = Group(name=team_name, entity_id=entity.id)
        db.add(team)
        await db.flush()

    for priority, (tto_minutes, ttr_minutes) in SLA_TARGETS.items():
        name = f"{entity.name} Priority {priority} SLA"
        existing = await db.scalar(select(SLA).where(SLA.entity_id == entity.id, SLA.name == name))
        if existing is None:
            db.add(
                SLA(
                    name=name,
                    entity_id=entity.id,
                    tto_minutes=tto_minutes,
                    ttr_minutes=ttr_minutes,
                    calendar=BUSINESS_HOURS,
                )
            )

    for event, subject, body in NOTIFICATION_TEMPLATES:
        existing = await db.scalar(
            select(NotificationTemplate).where(
                NotificationTemplate.entity_id == entity.id,
                NotificationTemplate.event == event,
            )
        )
        if existing is None:
            db.add(
                NotificationTemplate(
                    entity_id=entity.id,
                    event=event,
                    subject=subject,
                    body_jinja=body,
                )
            )

    rule_name = f"{entity.name} Priority 1 Default Team"
    rule = await db.scalar(
        select(BusinessRule).where(BusinessRule.entity_id == entity.id, BusinessRule.name == rule_name)
    )
    if rule is None:
        db.add(
            BusinessRule(
                entity_id=entity.id,
                name=rule_name,
                trigger="ON_CREATE",
                conditions={"field": "priority", "op": "eq", "value": 1},
                actions={"set_team": team.id},
                order=0,
            )
        )

    source = await db.scalar(select(FieldDefinition).where(FieldDefinition.key == "source"))
    if source is not None and not (source.options or {}).get("choices"):
        source.options = {"choices": ["PORTAL", "EMAIL", "PHONE", "API", "RECURRING"]}
    return created_templates


async def seed_profile_rights(profiles: dict[str, Profile]) -> list[str]:
    required_rights = {
        "Requester": {"ticket:own_only": True},
        "Technician": {
            "ticket:view_queue": True,
            "ticket:claim": True,
            "ticket:bulk_edit": True,
        },
        "Administrator": {
            "ticket:view_all": True,
            "ticket:claim": True,
            "ticket:bulk_edit": True,
            "ticket:assign_any": True,
        },
    }
    changed = []
    for name, rights_to_add in required_rights.items():
        profile = profiles[name]
        rights = dict(profile.rights or {})
        missing = {key: value for key, value in rights_to_add.items() if key not in rights}
        if missing:
            rights.update(missing)
            profile.rights = rights
            changed.append(f"{name}: {', '.join(sorted(missing))}")
    return changed


async def seed_ticket_type_field_defaults(db: AsyncSession) -> list[str]:
    rows = (
        await db.execute(
            select(TicketTypeDefinition, TicketTypeField, FieldDefinition)
            .join(TicketTypeField, TicketTypeField.ticket_type_id == TicketTypeDefinition.id)
            .join(FieldDefinition, FieldDefinition.id == TicketTypeField.field_definition_id)
            .where(TicketTypeDefinition.is_active.is_(True))
        )
    ).all()
    changed = []
    original_visible = ALL_ROLES
    for ticket_type, mapping, definition in rows:
        if definition.key not in (
            PORTAL_EDITABLE_FIELDS | SERVER_DERIVED_FIELDS | ADMIN_TECHNICIAN_FIELDS
        ):
            continue
        expected_editable = (
            ALL_ROLES
            if definition.key in ORIGINAL_REQUESTER_EDITABLE_FIELDS
            else HELPDESK_ROLES
        )
        if (
            mapping.visible_to_roles != original_visible
            or mapping.editable_by_roles != expected_editable
            or not mapping.show_on_portal
        ):
            continue

        visible_to_roles = (
            HELPDESK_ROLES if definition.key in SERVER_DERIVED_FIELDS else ALL_ROLES
        )
        editable_by_roles = (
            ALL_ROLES if definition.key in PORTAL_EDITABLE_FIELDS else HELPDESK_ROLES
        )
        show_on_portal = definition.key in PORTAL_EDITABLE_FIELDS
        if (
            mapping.visible_to_roles == visible_to_roles
            and mapping.editable_by_roles == editable_by_roles
            and mapping.show_on_portal == show_on_portal
        ):
            continue
        mapping.visible_to_roles = visible_to_roles
        mapping.editable_by_roles = editable_by_roles
        mapping.show_on_portal = show_on_portal
        changed.append(f"{ticket_type.name}.{definition.key}")
    return changed


async def seed() -> None:
    async with SessionLocal() as db:
        entity = await db.scalar(select(Entity).where(Entity.name == "SVC Infosystems"))
        if entity is None:
            entity = Entity(name="SVC Infosystems")
            db.add(entity)
            await db.flush()
        profiles: dict[str, Profile] = {}
        for name, interface, rights in [
            ("Requester", ProfileInterface.SELF_SERVICE.value, {"tickets:create": True, "tickets:read_own": True, "ticket:own_only": True}),
            ("Technician", ProfileInterface.HELPDESK.value, {"tickets:read": True, "tickets:update": True, "tickets:comment": True, "ticket:view_queue": True, "ticket:claim": True}),
            ("Administrator", ProfileInterface.HELPDESK.value, {"*": True, "ticket:view_all": True, "ticket:claim": True}),
        ]:
            profile = await db.scalar(select(Profile).where(Profile.name == name))
            if profile is None:
                profile = Profile(name=name, interface=interface, rights=rights)
                db.add(profile)
                await db.flush()
            profiles[name] = profile
        updated_profile_rights = await seed_profile_rights(profiles)
        updated_form_fields = await seed_ticket_type_field_defaults(db)
        users = [
            ("admin@svcinfosys.in", "SVC Administrator", "Administrator", os.environ["SEED_ADMIN_PASSWORD"]),
            ("aarav@svcinfosys.in", "Aarav Sharma", "Technician", os.environ["SEED_TECHNICIAN_PASSWORD"]),
            ("priya@svcinfosys.in", "Priya Nair", "Requester", os.environ["SEED_REQUESTER_PASSWORD"]),
        ]
        for email, name, profile_name, password in users:
            existing = await db.scalar(select(User).where(User.email == email))
            if existing is None:
                db.add(User(email=email, name=name, profile_id=profiles[profile_name].id, entity_id=entity.id, password_hash=hash_password(password)))
        created_templates = await seed_entity_defaults(db, entity)
        service_desk = await db.scalar(
            select(Group).where(
                Group.entity_id == entity.id,
                Group.name == f"{entity.name} Service Desk",
            )
        )
        technicians = await db.scalars(
            select(User).where(
                User.entity_id == entity.id,
                User.profile_id == profiles["Technician"].id,
                User.is_active.is_(True),
            )
        )
        if service_desk is not None:
            for technician in technicians:
                membership = await db.get(GroupMember, (service_desk.id, technician.id))
                if membership is None:
                    db.add(GroupMember(group_id=service_desk.id, user_id=technician.id))
        await db.commit()
        if updated_profile_rights:
            print("Added seeded profile rights: " + "; ".join(updated_profile_rights))
        if updated_form_fields:
            print("Updated default form visibility: " + ", ".join(updated_form_fields))
        if created_templates:
            print("Created seeded ticket templates: " + "; ".join(created_templates))


if __name__ == "__main__":
    asyncio.run(seed())
