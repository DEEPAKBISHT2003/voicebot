# Section 11 — Infrastructure

> **Cross-references:** [Docker Documentation](../10-docker/10-docker.md) | [Deployment Guide](../24-deployment/24-deployment.md) | [Configuration](../12-configuration/12-configuration.md)

---

## 11.1 Infrastructure Overview

VoiceBot runs on a single-node deployment using Docker Compose on an Ubuntu EC2 instance. There is no cloud-native PaaS, load balancer, or managed database — all components are self-hosted in containers.

```
┌─────────────────────────────────────────────────────────┐
│              Ubuntu EC2 (ip-172-31-6-132)               │
│                                                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │            Docker Compose Stack                  │   │
│  │                                                  │   │
│  │  Nginx :80 ──► React Static                      │   │
│  │              ──► interview-service :8000          │   │
│  │              ──► copilot-service :8001            │   │
│  │                                                  │   │
│  │  PostgreSQL 15 :5432 (internal only)             │   │
│  │                                                  │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  Storage: EBS Volume (8-30GB)                           │
│  ./interviews/ bind mount (audio + session files)       │
│                                                         │
└─────────────────────────────────────────────────────────┘
              │
              │ HTTPS (port 443 / SSL termination)
              │ or HTTP (port 80 for dev)
              ▼
         Internet
```

---

## 11.2 Nginx Configuration

**File:** `frontend-new/nginx.conf`

Nginx serves three roles:
1. **Static file server** — serves compiled React app
2. **HTTP reverse proxy** — routes `/api/*` to backend services
3. **WebSocket proxy** — proxies WebSocket upgrades

```nginx
server {
    listen 80;
    server_name _;

    # 1. Serve React static files
    root /usr/share/nginx/html;
    index index.html;

    # 2. React Router support (SPA fallback)
    location / {
        try_files $uri $uri/ /index.html;
    }

    # 3. Interview Service proxy (HTTP + WebSocket)
    location /api/ {
        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeouts for long-running interview sessions
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;

        # Route to interview service
        proxy_pass http://interview-service:8000;
    }

    # 4. Copilot Service proxy (separate path)
    location /api/ws/copilot/ {
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
        proxy_pass http://copilot-service:8001;
    }

    location /api/copilot/ {
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_pass http://copilot-service:8001;
    }

    # Gzip compression for static assets
    gzip on;
    gzip_types text/plain text/css application/json application/javascript;
}
```

**Critical nginx settings for real-time audio:**
- `proxy_read_timeout 3600s` — prevents Nginx from closing long-running WebSocket audio streams (default is 60s)
- `proxy_http_version 1.1` — required for WebSocket upgrade headers
- `Connection "upgrade"` — required for WebSocket handshake

---

## 11.3 Compute (EC2)

| Property | Value |
|---|---|
| Cloud | AWS |
| Instance Type | Not specified (minimum: t3.medium recommended) |
| OS | Ubuntu 22.04 LTS |
| Internal IP | 172.31.6.132 |
| Networking | VPC with security group |

**Recommended EC2 sizing:**

| Tier | Instance | Use Case |
|---|---|---|
| Development | t3.medium (2 vCPU, 4GB) | Single-user dev testing |
| Small Prod | t3.large (2 vCPU, 8GB) | 5-10 concurrent sessions |
| Medium Prod | t3.xlarge (4 vCPU, 16GB) | 20-30 concurrent sessions |

**Security Group Rules:**
```
Inbound:
  Port 80 (HTTP)  — 0.0.0.0/0 (or specific IPs)
  Port 443 (HTTPS) — 0.0.0.0/0 (with SSL)
  Port 22 (SSH)   — admin IPs only

Outbound:
  All traffic — 0.0.0.0/0 (for API calls to Deepgram/DeepSeek/Groq)
```

---

## 11.4 Storage

### EBS Root Volume
- Default: 8GB (insufficient for full Docker builds — see troubleshooting)
- **Recommended minimum: 30GB**
- Docker images alone: ~3-4GB total (Chromium is large)
- Audio recordings: ~50MB per hour of interview audio

### `interviews/` Bind Mount
- Stores all session data: JSON, WAV files, resume PDFs
- Not backed up automatically — production deployments need S3 sync or EBS snapshots
- WAV files (48kHz stereo → 16kHz mono after conversion) average 3-5MB per session

### PostgreSQL Volume
- Docker named volume `postgres_data`
- Stores all structured session data
- Must be included in backup strategy

---

## 11.5 SSL / HTTPS

**Current state:** No SSL configured by default. HTTP only on port 80.

**Recommended SSL setup with Let's Encrypt:**
```bash
# Install certbot on the host
sudo apt install certbot python3-certbot-nginx

# Obtain certificate
sudo certbot --nginx -d yourdomain.com

# Auto-renewal (certbot creates a cron job)
sudo certbot renew --dry-run
```

**Nginx SSL configuration (to add):**
```nginx
server {
    listen 443 ssl;
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
    # ... rest of config
}

server {
    listen 80;
    return 301 https://$host$request_uri;
}
```

> **Important:** WebSocket URLs in frontend must use `wss://` when running over HTTPS. The `useInterviewAudio.ts` hook correctly detects `window.location.protocol === 'https:'` and switches to `wss:`.

---

## 11.6 Health Checks

### PostgreSQL Health Check
```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
  interval: 10s
  timeout: 5s
  retries: 5
```

### Application Health Endpoints

Both FastAPI services expose a `/health` endpoint (standard FastAPI pattern):
```
GET /health → {"status": "ok"}
```

### Docker Container Status
```bash
docker compose ps  # View all container statuses
docker stats       # Real-time CPU/memory per container
```

---

## 11.7 Logging

**Python services** use `loguru` for structured logging output to stdout:
```python
from loguru import logger
logger.info("Session started: {}", session_id)
logger.error("Pipeline failed: {}", error)
```

**Docker Compose** captures stdout/stderr from all containers:
```bash
docker compose logs -f                    # All containers
docker compose logs -f interview-service  # Specific container
docker compose logs --tail=100 copilot-service
```

**Log Retention:** Default Docker log driver (json-file), no rotation configured. In production, configure log rotation:
```yaml
services:
  interview-service:
    logging:
      driver: "json-file"
      options:
        max-size: "50m"
        max-file: "3"
```

---

## 11.8 Monitoring

**Current state:** No monitoring stack (Prometheus/Grafana/Datadog) is configured.

**Inferred basic monitoring options:**
- Docker `docker stats` — container CPU/memory
- PostgreSQL `pg_stat_activity` — active DB queries
- Nginx access logs — request rates

**Recommended production monitoring stack:**
```
Prometheus ─► scrape metrics from services
Grafana ─► dashboards for sessions/min, LLM latency, error rates
Loki ─► aggregate logs from Docker containers
Alertmanager ─► alert on service down, high error rates
```

---

## 11.9 Disk Space Management

**Critical issue encountered:** Docker build layers + Playwright Chromium (~600MB) can exhaust disk on small instances.

**Recommended maintenance commands:**
```bash
# Check available disk
df -h

# Free Docker build cache (safe)
docker builder prune -f

# Remove unused images/containers (safe - running containers unaffected)
docker system prune -f

# Remove ALL unused images including tagged (careful in production)
docker system prune -a -f

# Check Docker disk usage breakdown
docker system df -v

# Monitor disk usage continuously
watch -n 5 df -h
```

---

*Next: [Section 12 — Configuration →](../12-configuration/12-configuration.md)*

