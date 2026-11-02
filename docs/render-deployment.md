# Deploy SVC ServiceFlow on Render

The root `render.yaml` defines the production deployment:

- A public Next.js web service.
- A private FastAPI service, with a persistent disk mounted at `/app/data/documents`.
- A Celery worker and beat process.
- Managed Postgres and Key Value services.

The API is private; the Next.js server proxies `/api/v1/*` to it over Render's private network. The frontend image receives `API_INTERNAL_HOSTPORT` during its Docker build. The local Docker Compose setup continues to use `api:8000`.

## Set up the Blueprint

1. Push the `ui-fixes` branch to GitHub and connect that branch as a new Blueprint in the Render dashboard.
2. Review the services and plans in the Blueprint before deploying. The persistent API disk and production database/compute plans are paid Render resources.
3. During the Blueprint prompt, set:
   - `CORS_ORIGINS`: the exact public frontend origin, such as `https://svc-serviceflow-web.onrender.com`.
   - `APP_BASE_URL`: the exact same public frontend URL; invitations link back to it.
   - `SMTP_USERNAME` and `SMTP_PASSWORD`: the SMTP credentials from your verified Brevo account.
   - `MAIL_FROM`: a sender address on the domain verified with Brevo.
   - `SEED_ADMIN_EMAIL`, `SEED_ADMIN_NAME`, and `SEED_ADMIN_PASSWORD`: the initial administrator account. Use a unique password of at least 16 characters and no more than 72 UTF-8 bytes; keep these values in Render's environment settings.
4. Configure the sender domain's SPF and DKIM records with your DNS provider before relying on invitation or ticket emails.
5. Deploy the Blueprint and wait for the public web service to report healthy.
6. Sign in with the initial admin account, then invite technicians and requesters from **People → Users**.

The production seed initializes entity defaults and creates only the configured first administrator. It does not create the local demo technician/requester accounts, does not overwrite existing passwords, and does not auto-assign invited technicians to teams. Subsequent technicians and requesters should be invited through the application.

Do not set `DEBUG=true` or `ALLOW_DEMO_SEED=1` in Render. The database is migrated by the API start command. No Alembic migration is added by this deployment setup.

## Important storage note

Render's disk is attached only to the API service. Ticket upload and download requests go through that API, so those files remain on the persistent disk. Do not scale the API above one instance while using local disk storage: a Render disk is not shared across instances. For horizontal API scaling, migrate uploads to shared object storage first.
