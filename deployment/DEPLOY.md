# Optificial.AI — Production Deployment Guide

> **Target**: Ubuntu 22.04 LTS bare-metal / EC2 instance  
> **Stack**: Docker Compose (Postgres + Backend + Extractor + Simpro) + bare-metal Nginx  
> **Database**: Self-hosted PostgreSQL 16 (RDS NOT required)

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Server Setup](#2-server-setup)
3. [Clone & Configure](#3-clone--configure)
4. [Generate Secrets](#4-generate-secrets)
5. [Build & Start](#5-build--start-docker-compose)
6. [Bare-metal Nginx Setup](#6-bare-metal-nginx-setup)
7. [SSL with Certbot (Let's Encrypt)](#7-ssl-with-certbot-lets-encrypt)
8. [Health Checks & Verification](#8-health-checks--verification)
9. [Maintenance & Operations](#9-maintenance--operations)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Prerequisites

| Requirement | Minimum |
|---|---|
| **OS** | Ubuntu 22.04 LTS (or Debian 12) |
| **RAM** | 4 GB (8 GB recommended) |
| **Disk** | 30 GB SSD |
| **Docker** | 24.x+ |
| **Docker Compose** | v2.20+ (plugin, not standalone) |
| **Domain** | DNS A-record pointing to server IP |
| **Ports** | 80, 443 open in security group / firewall |

---

## 2. Server Setup

### 2.1 Install Docker

```bash
# Update system
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker (official method)
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add your user to the docker group (no sudo needed for docker commands)
sudo usermod -aG docker $USER
newgrp docker

# Verify
docker --version
docker compose version
```

### 2.2 Install Nginx

```bash
sudo apt-get install -y nginx
sudo systemctl enable nginx
sudo systemctl start nginx

# Verify nginx is running
curl -I http://localhost
```

### 2.3 Firewall (if using UFW)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'   # ports 80 + 443
sudo ufw enable
sudo ufw status
```

---

## 3. Clone & Configure

### 3.1 Clone the Repository

```bash
# Create the application directory
sudo mkdir -p /opt/optificial
sudo chown -R $USER:$USER /opt/optificial

# Clone the repo
cd /opt/optificial
git clone https://github.com/YOUR_ORG/Optificial.AI.git .
```

### 3.2 Configure Environment Files

The `deployment/` folder contains three `.env` template files. Edit each one with your production values:

```bash
cd /opt/optificial/deployment

# Edit backend config (most variables live here)
nano backend.env

# Edit extractor config (usually just LOG_LEVEL)
nano extractor.env

# Edit simpro MCP server config
nano simpro.env
```

> [!CAUTION]
> **Never commit `.env` files with real secrets to Git.** The templates contain `CHANGE_ME` placeholders — replace every single one.

---

## 4. Generate Secrets

Run these commands on the server to generate cryptographically secure values:

### 4.1 Fernet Key (for encrypting Simpro credentials at rest)

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Copy the output into `backend.env` → `FERNET_KEY=`

> [!WARNING]
> **Never change the Fernet key after first use.** All encrypted data (Simpro tokens, API keys stored per-org) becomes permanently unreadable if the key changes.

### 4.2 JWT Secret

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Copy into `backend.env` → `JWT_SECRET=`

### 4.3 Superadmin Token

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy into `backend.env` → `SUPERADMIN_TOKEN=`

### 4.4 Postgres Password

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

Update **both** places:
- `docker-compose.yml` → `POSTGRES_PASSWORD` (or set in shell env)
- `backend.env` → inside `DATABASE_URL=postgresql://optificial:<PASSWORD>@postgres:5432/optificial`

> [!TIP]
> You can also set the Postgres password via shell environment:
> ```bash
> export POSTGRES_PASSWORD="your_strong_password_here"
> ```
> The `docker-compose.yml` already references `${POSTGRES_PASSWORD:-optificial_prod_2026}` with a fallback default.

---

## 5. Build & Start (Docker Compose)

```bash
cd /opt/optificial/deployment

# Build all images and start in detached mode
docker compose up -d --build

# Watch the logs during first startup (Ctrl+C to exit)
docker compose logs -f
```

### What happens on first start:

1. **PostgreSQL** initializes the database and creates the `optificial` database
2. **Backend** waits for Postgres health check, then:
   - Runs `init_db()` which creates all tables via SQLAlchemy
   - Seeds platform settings with default LLM values
   - Runs encryption migration for org credentials
3. **Extractor** and **Simpro** start independently (stateless services)

### Verify all containers are running:

```bash
docker compose ps
```

Expected output:
```
NAME                    STATUS              PORTS
optificial-backend      Up (healthy)        127.0.0.1:8001->8001/tcp
optificial-extractor    Up                  127.0.0.1:8010->8010/tcp
optificial-postgres     Up (healthy)        127.0.0.1:5432->5432/tcp
optificial-simpro       Up                  127.0.0.1:8000->8000/tcp
```

---

## 6. Bare-metal Nginx Setup

Since the application containers bind to `127.0.0.1`, Nginx on the host acts as the public-facing reverse proxy.

### 6.1 Create the Nginx Site Config

```bash
sudo nano /etc/nginx/sites-available/optificial
```

Paste the following configuration:

```nginx
# ═══════════════════════════════════════════════════════════════════════════
# Optificial.AI — Nginx Reverse Proxy (Backend API)
# ═══════════════════════════════════════════════════════════════════════════

# Rate limiting zone (10 requests/sec per IP for login endpoint)
limit_req_zone $binary_remote_addr zone=login:10m rate=10r/s;

# ── Backend API ──────────────────────────────────────────────────────────
server {
    listen 80;
    server_name api.optificial.com;

    # Redirect HTTP → HTTPS (uncomment after Certbot setup)
    # return 301 https://$host$request_uri;

    # ── API Proxy ────────────────────────────────────────────────────────
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE Streaming (critical for chat responses)
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;

        # Upload size limit (for document extraction)
        client_max_body_size 50M;
    }

    # ── Rate-limited auth endpoint ───────────────────────────────────────
    location /api/auth/ {
        limit_req zone=login burst=20 nodelay;

        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # ── Health check (for load balancers / monitoring) ───────────────────
    location /health {
        proxy_pass http://127.0.0.1:8001/health;
        access_log off;
    }
}

# ── Extractor Service (internal only, optional external exposure) ────────
# Uncomment if you need direct access to the extractor from outside.
# Otherwise the backend calls it internally via Docker network.
#
# server {
#     listen 80;
#     server_name extractor.optificial.com;
#
#     location / {
#         proxy_pass http://127.0.0.1:8010;
#         proxy_set_header Host $host;
#         proxy_set_header X-Real-IP $remote_addr;
#         client_max_body_size 50M;
#     }
# }
```

### 6.2 Enable the Site

```bash
# Create symlink to enable the site
sudo ln -sf /etc/nginx/sites-available/optificial /etc/nginx/sites-enabled/

# Remove the default site
sudo rm -f /etc/nginx/sites-enabled/default

# Test the config for syntax errors
sudo nginx -t

# Reload Nginx
sudo systemctl reload nginx
```

### 6.3 Verify Nginx

```bash
# From the server itself
curl -s http://localhost/health

# From outside (replace with your domain)
curl -s http://api.optificial.com/health
```

---

## 7. SSL with Certbot (Let's Encrypt)

### 7.1 Install Certbot

```bash
sudo apt-get install -y certbot python3-certbot-nginx
```

### 7.2 Obtain SSL Certificate

```bash
sudo certbot --nginx -d api.optificial.com
```

Certbot will:
1. Verify domain ownership via HTTP challenge
2. Obtain a free SSL certificate
3. **Automatically modify your Nginx config** to add SSL listeners on port 443
4. Set up HTTP → HTTPS redirect

### 7.3 Verify Auto-Renewal

```bash
# Test renewal (dry run)
sudo certbot renew --dry-run

# Certbot installs a systemd timer for auto-renewal
sudo systemctl status certbot.timer
```

### 7.4 Post-SSL Nginx Config

After Certbot runs, your config will be automatically updated. Verify it:

```bash
sudo nginx -t
sudo systemctl reload nginx

# Test HTTPS
curl -s https://api.optificial.com/health
```

---

## 8. Health Checks & Verification

### 8.1 Service Health

```bash
# All containers running?
docker compose -f /opt/optificial/deployment/docker-compose.yml ps

# Backend health
curl -s http://127.0.0.1:8001/health | python3 -m json.tool

# Simpro MCP health
curl -s http://127.0.0.1:8000/health | python3 -m json.tool

# Extractor (should return 404 or a root response — means it's alive)
curl -s http://127.0.0.1:8010/ -o /dev/null -w "%{http_code}"

# Postgres connectivity
docker compose -f /opt/optificial/deployment/docker-compose.yml exec postgres pg_isready -U optificial
```

### 8.2 Database Verification

```bash
# Connect to PostgreSQL
docker compose -f /opt/optificial/deployment/docker-compose.yml exec postgres \
    psql -U optificial -d optificial -c "\dt"

# Should list tables: users, organizations, org_memberships, usage_records, etc.
```

### 8.3 End-to-End Test

```bash
# Test the API through Nginx (HTTPS after Certbot)
curl -s https://api.optificial.com/health

# Test auth endpoint
curl -s -X POST https://api.optificial.com/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"test@example.com","password":"test"}'
```

---

## 9. Maintenance & Operations

### 9.1 View Logs

```bash
# All services
docker compose -f /opt/optificial/deployment/docker-compose.yml logs -f

# Specific service
docker compose -f /opt/optificial/deployment/docker-compose.yml logs -f backend
docker compose -f /opt/optificial/deployment/docker-compose.yml logs -f postgres
docker compose -f /opt/optificial/deployment/docker-compose.yml logs -f simpro
docker compose -f /opt/optificial/deployment/docker-compose.yml logs -f extractor
```

### 9.2 Restart Services

```bash
# Restart all
docker compose -f /opt/optificial/deployment/docker-compose.yml restart

# Restart single service (no downtime for others)
docker compose -f /opt/optificial/deployment/docker-compose.yml restart backend
```

### 9.3 Update / Redeploy

```bash
cd /opt/optificial

# Pull latest code
git pull origin main

# Rebuild and restart (zero-downtime for Postgres since its image didn't change)
cd deployment
docker compose up -d --build
```

### 9.4 Database Backup

```bash
# Create a backup
docker compose -f /opt/optificial/deployment/docker-compose.yml exec postgres \
    pg_dump -U optificial optificial > /opt/optificial/backups/backup_$(date +%Y%m%d_%H%M%S).sql

# Automated daily backup (add to crontab)
# crontab -e
# 0 2 * * * docker compose -f /opt/optificial/deployment/docker-compose.yml exec -T postgres pg_dump -U optificial optificial > /opt/optificial/backups/backup_$(date +\%Y\%m\%d).sql
```

### 9.5 Database Restore

```bash
# Stop the backend first
docker compose -f /opt/optificial/deployment/docker-compose.yml stop backend

# Restore from backup
cat /opt/optificial/backups/backup_20260625.sql | \
    docker compose -f /opt/optificial/deployment/docker-compose.yml exec -T postgres \
    psql -U optificial -d optificial

# Start backend again
docker compose -f /opt/optificial/deployment/docker-compose.yml start backend
```

---

## 10. Troubleshooting

### Container won't start

```bash
# Check container exit code and logs
docker compose -f /opt/optificial/deployment/docker-compose.yml ps -a
docker compose -f /opt/optificial/deployment/docker-compose.yml logs backend --tail 50
```

### Backend can't connect to Postgres

1. Check Postgres is healthy: `docker compose ps postgres`
2. Verify `DATABASE_URL` in `backend.env` uses `postgres` (container name), not `localhost`
3. Check the password matches between `docker-compose.yml` and `backend.env`

### Nginx 502 Bad Gateway

1. Check if the backend container is running: `docker compose ps backend`
2. Check if the port mapping is correct: `docker compose port backend 8001`
3. Check Nginx config: `sudo nginx -t`
4. Check Nginx error log: `sudo tail -f /var/log/nginx/error.log`

### SSE Streaming not working

Ensure these are in your Nginx config:
```nginx
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_buffering off;
proxy_cache off;
```

### Permission denied on logs volume

```bash
# Fix ownership inside the container
docker compose -f /opt/optificial/deployment/docker-compose.yml exec backend \
    chown -R nobody:nogroup /app/backend/logs
```

### Out of disk space

```bash
# Clean Docker build cache
docker system prune -af --volumes

# Check disk usage
df -h
docker system df
```

---

## Architecture Diagram

```
                    ┌─────────────────────────────────────────┐
                    │              Internet                    │
                    └──────────────────┬──────────────────────┘
                                       │
                              ┌────────▼────────┐
                              │   Nginx (Host)   │
                              │   :80 / :443     │
                              │   + SSL (Certbot)│
                              └────────┬─────────┘
                                       │ proxy_pass
                    ┌──────────────────┼──────────────────────┐
                    │          Docker Network                  │
                    │         (optificial-net)                 │
                    │                  │                       │
          ┌────────▼────────┐         │         ┌─────────────▼──────┐
          │   Backend :8001  │         │         │  Extractor :8010   │
          │  (FastAPI +      │         │         │  (PDF/DOCX/OCR)    │
          │   4 Agent Libs)  │         │         └────────────────────┘
          └────────┬─────────┘         │
                   │                   │
          ┌────────▼────────┐  ┌──────▼──────────┐
          │ PostgreSQL :5432 │  │ Simpro MCP :8000│
          │ (pgdata volume)  │  │ (ERP Bridge)    │
          └──────────────────┘  └─────────────────┘
```

---

## Quick Reference

| Action | Command |
|---|---|
| Start all | `cd /opt/optificial/deployment && docker compose up -d` |
| Stop all | `docker compose down` |
| Rebuild + restart | `docker compose up -d --build` |
| View logs | `docker compose logs -f` |
| DB shell | `docker compose exec postgres psql -U optificial -d optificial` |
| Backup DB | `docker compose exec postgres pg_dump -U optificial optificial > backup.sql` |
| Nginx test | `sudo nginx -t` |
| Nginx reload | `sudo systemctl reload nginx` |
| SSL renew | `sudo certbot renew` |
