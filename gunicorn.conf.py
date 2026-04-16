"""
Vantage — Gunicorn Production Configuration
https://docs.gunicorn.org/en/stable/settings.html
"""
import multiprocessing

# ── Server socket ─────────────────────────────────────────────────────────
# PORT is injected by Heroku/Render/Railway; default to 8000 locally.
import os
bind    = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
backlog = 2048

# ── Worker processes ──────────────────────────────────────────────────────
# gthread supports long-lived SSE streams without blocking other requests.
# Each worker gets `threads` concurrent request slots.
workers           = multiprocessing.cpu_count() * 2 + 1
worker_class      = "gthread"
threads           = 4
worker_connections = 1000

# Long timeout for SSE endpoints (/api/*/events) that stay open indefinitely.
timeout   = 120
keepalive = 5

# Graceful shutdown
graceful_timeout = 30

# Restart workers periodically to prevent memory leaks from long-running processes.
max_requests        = 1000
max_requests_jitter = 50   # adds ±50 randomness so workers don't restart at once

# ── Logging ──────────────────────────────────────────────────────────────
# "-" means stdout/stderr — captured by the platform's log aggregator.
accesslog  = "-"
errorlog   = "-"
loglevel   = "info"
access_log_format = (
    '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s '
    '"%(f)s" "%(a)s" %(D)sμs'
)

# ── Security ──────────────────────────────────────────────────────────────
limit_request_line   = 4094
limit_request_fields = 100

# Trust X-Forwarded-For from the reverse proxy (Render/Heroku ingress).
forwarded_allow_ips     = "*"
secure_scheme_headers   = {"X-Forwarded-Proto": "https"}
