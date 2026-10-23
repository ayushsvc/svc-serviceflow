# Production deployment guide

The included Compose file is a development-friendly single-service deployment, not a complete production architecture. Before production:

1. Run behind a managed TLS-terminating reverse proxy and set `NODE_ENV=production`.
2. Supply secrets through the platform secret manager, not a checked-in `.env` file. Use a randomly generated `JWT_SECRET`; never use the local fallback.
3. Replace SQLite with PostgreSQL for high-volume or multi-instance deployments, retaining the same relational entities and migration discipline.
4. Move attachments to private object storage. Serve downloads through authenticated, short-lived URLs rather than a public static path.
5. Configure real outbound email, password reset tokens with expiry and one-time use, and an MFA provider.
6. Add centralized logs, error monitoring, health checks, rate-limit storage, vulnerability scanning, and database/attachment backups with restore tests.
7. Create the first administrator through a one-time provisioning command or identity provider; do not retain sample accounts.
8. Put the app behind a WAF and restrict database/network access to the application subnet.

Use rolling deployments only after database migrations are applied and tested against a backup copy.
