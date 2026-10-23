import asyncio
import os

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import Entity, Profile, ProfileInterface, User


async def seed() -> None:
    async with SessionLocal() as db:
        entity = await db.scalar(select(Entity).where(Entity.name == "SVC Infosystems"))
        if entity is None:
            entity = Entity(name="SVC Infosystems")
            db.add(entity)
            await db.flush()
        profiles: dict[str, Profile] = {}
        for name, interface, rights in [
            ("Requester", ProfileInterface.SELF_SERVICE.value, {"tickets:create": True, "tickets:read_own": True}),
            ("Technician", ProfileInterface.HELPDESK.value, {"tickets:read": True, "tickets:update": True, "tickets:comment": True}),
            ("Administrator", ProfileInterface.HELPDESK.value, {"*": True}),
        ]:
            profile = await db.scalar(select(Profile).where(Profile.name == name))
            if profile is None:
                profile = Profile(name=name, interface=interface, rights=rights)
                db.add(profile)
                await db.flush()
            profiles[name] = profile
        users = [
            ("admin@svcinfosys.in", "SVC Administrator", "Administrator", os.environ["SEED_ADMIN_PASSWORD"]),
            ("aarav@svcinfosys.in", "Aarav Sharma", "Technician", os.environ["SEED_TECHNICIAN_PASSWORD"]),
            ("priya@svcinfosys.in", "Priya Nair", "Requester", os.environ["SEED_REQUESTER_PASSWORD"]),
        ]
        for email, name, profile_name, password in users:
            existing = await db.scalar(select(User).where(User.email == email))
            if existing is None:
                db.add(User(email=email, name=name, profile_id=profiles[profile_name].id, entity_id=entity.id, password_hash=hash_password(password)))
        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed())
