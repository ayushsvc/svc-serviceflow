# Low-Cost Production Deployment Guide ($4/Month)

This guide explains how to deploy **SVC ServiceFlow** on a single low-cost Cloud VPS (e.g., Hetzner Cloud €3.79/mo, DigitalOcean $4–$6/mo) with automatic SSL and minimal memory consumption (<400 MB RAM).

---

## 1. Architecture Overview

```
                      Internet
                         │
                 HTTPS (Port 80/443)
                         │
           ┌─────────────▼─────────────┐
           │       Caddy Proxy         │ (Auto Let's Encrypt SSL)
           └─────────────┬─────────────┘
                         │
            ┌────────────┴────────────┐
            │                         │
     /api/v1/* traffic           UI Web traffic
            │                         │
  ┌─────────▼──────────┐    ┌─────────▼──────────┐
  │   FastAPI Backend  │    │  Next.js Frontend  │
  │ • In-Process Cron  │    │ (or Cloudflare     │
  │ • Async Mailer     │    │  Pages - Free)     │
  │ • Embedded KV      │    └────────────────────┘
  └─────────┬──────────┘
            │
  ┌─────────▼──────────┐
  │ PostgreSQL 16      │
  │ (Alpine Container) │
  └────────────────────┘
```

### Resource Consumption
- **Total Containers:** 3 (Caddy + FastAPI + PostgreSQL)
- **Total RAM:** ~350 MB – 400 MB
- **Monthly Cost:** **€3.79 / month** on Hetzner CX22 (2 vCPU, 4GB RAM)

---

## 2. Server Setup (One-Time)

### Step 1: Provision your VPS
1. Create an account on [Hetzner Cloud](https://www.hetzner.com/cloud) or [DigitalOcean](https://m.do.co).
2. Create an **Ubuntu 24.04** or **Debian 12** server:
   - **Hetzner CX22:** €3.79/month (2 vCPU, 4 GB RAM, 40 GB NVMe).
   - Or **DigitalOcean Droplet:** $4–$6/month.

### Step 2: Install Docker
Run on your server:
```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
```

---

## 3. Deployment Steps

### Step 1: Upload Project & Set Environment
1. Copy the project files to `/opt/svc-serviceflow` on your server.
2. Create `.env` by copying `.env.example`:
```bash
cp .env.example .env
```
3. Edit `.env` with strong production values:
```bash
# Domain & App URL
APP_BASE_URL=https://desk.yourdomain.com
CORS_ORIGINS=https://desk.yourdomain.com

# PostgreSQL
POSTGRES_DB=svc
POSTGRES_USER=svc
POSTGRES_PASSWORD=YOUR_STRONG_DATABASE_PASSWORD
DATABASE_URL=postgresql+asyncpg://svc:YOUR_STRONG_DATABASE_PASSWORD@db:5432/svc

# Security & Secrets (Must be at least 32 characters)
JWT_SECRET=YOUR_64_CHAR_RANDOM_SECRET_KEY
SEED_ADMIN_PASSWORD=YOUR_INITIAL_ADMIN_PASSWORD

# In-Memory KV (No Redis container needed)
REDIS_URL=memory://

# Email (Brevo or your custom SMTP)
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USERNAME=your-brevo-login
SMTP_PASSWORD=your-brevo-smtp-key
SMTP_STARTTLS=true
MAIL_FROM=support@yourdomain.com
```

### Step 2: Configure Domain in `Caddyfile`
Edit `Caddyfile` on the server and replace `{$DOMAIN:localhost}` with your domain:
```caddy
desk.yourdomain.com {
    encode gzip zstd
    reverse_proxy web:3000
}
```

### Step 3: Start the Application
Run:
```bash
docker compose -f docker-compose.production.yml up -d --build
```

Caddy will automatically provision a free TLS/SSL certificate from Let's Encrypt. Your application is live at `https://desk.yourdomain.com`!

---

## 4. (Optional) Zero-Cost Frontend on Cloudflare Pages

If you want your frontend hosted **100% free with worldwide edge caching**:
1. Run static build on your machine:
   ```bash
   npm run export:frontend
   ```
2. Deploy the `frontend/out` folder to **Cloudflare Pages** (Free Tier).
3. Set your backend URL to `https://api.yourdomain.com`.

---

## 5. Automated Database Backups

Add a daily cron job on the server to automatically back up your PostgreSQL database:
```bash
crontab -e
```
Add this line:
```cron
0 3 * * * docker exec -t $(docker ps -qf "name=db") pg_dump -U svc svc | gzip > /opt/backups/svc_$(date +\%F).sql.gz
```
