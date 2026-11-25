# SVC ServiceFlow

**Request to Resolution**

An IT service management (ITSM) platform for SVC Infosystems. Requesters raise tickets with attachments. Administrators review the unassigned queue and assign work. Technicians work their own and their teams' tickets.

## Features

- **Ticket intake:** Title, rich-text description, up to 5 attachments (paste with Ctrl+V), priority Low / Medium / High / Critical.
- **Admin queue:** "Unassigned" and "All tickets" tabs, inline assign, ticket detail review.
- **Technician queue:** own and team tickets, with an atomic claim controlled by the right `ticket:self_claim`.
- **Combinable filters:** assignee, requester, status, priority, ticket type, category and a created/updated date range. Filters persist in the URL, and saved filter profiles can be reused.
- **Attachments:** content validation, soft delete, private follow-ups visible to staff only.
- **SLA and notifications:** SLA deadlines and breach job, with email through Celery.
- **Security:** rights-based access, entity isolation on every query, server-side HTML sanitising, audit log.

## Roles

Administrator, Technician and Requester. Visibility is controlled by rights, not profile names.

## Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 14, TypeScript |
| API | FastAPI, async SQLAlchemy, Alembic |
| Data | PostgreSQL 16, Redis |
| Jobs | Celery worker and beat |
| Email (dev) | MailHog |
| Runtime | Docker Compose |

## Quick start

```powershell
Copy-Item .env.example .env
# edit .env: set POSTGRES_PASSWORD, DATABASE_URL, JWT_SECRET
docker compose up -d --build
docker compose run --rm api alembic upgrade head
docker compose run --rm api python -m app.seed
```

| Service | URL |
|---|---|
| Web app | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| MailHog | http://localhost:18025 |

The seed creates development accounts only. Change their passwords before any shared use, and never commit `.env`.

## Demo data (development only)

```powershell
docker compose run --rm -e DEBUG=true -e ALLOW_DEMO_SEED=1 api python -m app.seed_demo
docker compose run --rm -e DEBUG=true -e ALLOW_DEMO_SEED=1 api python -m app.seed_demo --remove
```

## Tests

```powershell
docker compose up -d postgres redis mailhog worker
docker compose run --rm api pytest -q
cd frontend; npm run build
```

## Repository layout

```
backend/    FastAPI app, Alembic migrations, tests
frontend/   Next.js app
```

## Notes

- Database changes need a new Alembic migration with a working downgrade.
- `docker compose down -v` deletes all local data.
- Run behind HTTPS with strong secrets before any public deployment.
