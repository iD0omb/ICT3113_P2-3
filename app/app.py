import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import requests
from flask import Flask, g, jsonify, request
from psycopg.rows import dict_row
from werkzeug.exceptions import HTTPException
from psycopg_pool import ConnectionPool

CATEGORIES = [
    "Bank account or service",
    "Consumer loan",
    "Credit card",
    "Credit reporting",
    "Debt collection",
    "Money transfer or service",
    "Mortgage",
]

PROMPT_VERSION = "v1"
SYSTEM_PROMPT = """You classify customer complaint tickets for a financial services company.
Assign exactly one category from this list:
- Bank account or service: checking/savings accounts, deposits, overdrafts, account opening/closing, bank fees.
- Consumer loan: vehicle loans/leases, personal loans, installment loans.
- Credit card: credit card billing, charges, fees, rewards, card account management.
- Credit reporting: errors on credit reports, disputes with credit bureaus, identity theft on reports.
- Debt collection: collectors contacting the consumer, debts not owed, collection practices.
- Money transfer or service: wire transfers, PayPal and similar services, remittances, money orders.
- Mortgage: home loans, servicing, escrow, modification, foreclosure, refinancing.
Respond with JSON only: {"category": "<one category from the list>"}"""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"category": {"type": "string", "enum": CATEGORIES}},
    "required": ["category"],
}

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
MODEL = os.environ["MODEL"]
SEED = int(os.environ.get("SEED", "42"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0"))
THINK = os.environ.get("THINK", "false").strip().lower()
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "600"))
DATABASE_URL = os.environ["DATABASE_URL"]
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "16"))
LOG_PATH = os.environ.get("LOG_PATH", "requests.log")


def _setup_logger():
    os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
    logger = logging.getLogger("triage")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    fmt = logging.Formatter("%(message)s")
    for handler in (logging.FileHandler(LOG_PATH), logging.StreamHandler()):
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return logger


log = _setup_logger()


def log_event(**fields):
    fields["ts"] = datetime.now(timezone.utc).isoformat()
    log.info(json.dumps(fields))


def ms_since(t):
    return round((time.perf_counter() - t) * 1000, 1)


# --- Storage ---------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id BIGSERIAL PRIMARY KEY,
    narrative TEXT NOT NULL,
    category TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

pool = ConnectionPool(
    DATABASE_URL,
    min_size=1,
    max_size=DB_POOL_SIZE,
    kwargs={"row_factory": dict_row},
    open=True,
)


def init_db():
    with pool.connection() as conn:
        conn.execute(SCHEMA)


# --- Model backend ---------------------------------------------------------

_session = requests.Session()
_session.mount("http://", requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=64))
_no_think_support = set()
_no_think_lock = threading.Lock()


class ClassificationError(Exception):
    pass


def classify(narrative):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": narrative},
        ],
        "format": RESPONSE_SCHEMA,
        "stream": False,
        "options": {"temperature": TEMPERATURE, "seed": SEED},
    }
    if THINK in ("true", "false") and MODEL not in _no_think_support:
        payload["think"] = THINK == "true"

    resp = _session.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT)
    if resp.status_code == 400 and "think" in resp.text.lower() and "think" in payload:
        # Model has no thinking mode; remember that and retry without the flag.
        with _no_think_lock:
            _no_think_support.add(MODEL)
        payload.pop("think")
        resp = _session.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT)
    if resp.status_code != 200:
        raise ClassificationError(f"ollama HTTP {resp.status_code}: {resp.text[:300]}")

    body = resp.json()
    raw = body.get("message", {}).get("content", "")
    try:
        category = json.loads(raw)["category"]
    except (ValueError, KeyError, TypeError):
        raise ClassificationError(f"unparseable model output: {raw[:300]!r}")
    if category not in CATEGORIES:
        raise ClassificationError(f"category not in label set: {category!r}")

    timings = {
        k: body.get(k)
        for k in (
            "total_duration", "load_duration", "prompt_eval_count",
            "prompt_eval_duration", "eval_count", "eval_duration",
        )
    }
    return category, timings


# --- HTTP API --------------------------------------------------------------

app = Flask(__name__)
init_db()


@app.before_request
def start_request():
    g.request_id = uuid.uuid4().hex
    g.started = time.perf_counter()
    g.log_fields = {}


@app.after_request
def log_request(response):
    log_event(event="request", request_id=g.request_id,
              run_id=request.headers.get("X-Run-Id"), method=request.method,
              path=request.path, status=response.status_code,
              total_ms=ms_since(g.started), **g.log_fields)
    return response


@app.errorhandler(Exception)
def unhandled_error(exc):
    if isinstance(exc, HTTPException):
        return exc
    g.log_fields["error"] = f"{type(exc).__name__}: {exc}"
    return jsonify(error="internal error"), 500


@app.post("/tickets")
def create_ticket():
    if request.is_json:
        narrative = (request.get_json(silent=True) or {}).get("narrative")
    else:
        narrative = request.get_data(as_text=True)
    if not isinstance(narrative, str) or not narrative.strip():
        g.log_fields["error"] = "missing or empty narrative"
        return jsonify(error="request body must contain a non-empty 'narrative'"), 400
    narrative = narrative.strip()
    g.log_fields.update(model=MODEL, prompt_version=PROMPT_VERSION, chars=len(narrative))

    t = time.perf_counter()
    try:
        category, timings = classify(narrative)
    except (ClassificationError, requests.RequestException) as exc:
        g.log_fields.update(error=str(exc), classify_ms=ms_since(t))
        return jsonify(error="classification failed", detail=str(exc)), 502
    g.log_fields.update(category=category, classify_ms=ms_since(t), **timings)

    t = time.perf_counter()
    with pool.connection() as conn:
        ticket_id = conn.execute(
            "INSERT INTO tickets (narrative, category, model, prompt_version) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (narrative, category, MODEL, PROMPT_VERSION),
        ).fetchone()["id"]
    g.log_fields.update(ticket_id=ticket_id, db_ms=ms_since(t))

    return jsonify(id=ticket_id, category=category, model=MODEL,
                   latency_ms=ms_since(g.started)), 201


@app.get("/search")
def search():
    q = request.args.get("q", "").strip()
    g.log_fields["q"] = q
    if not q:
        g.log_fields["error"] = "missing q"
        return jsonify(error="query parameter 'q' is required"), 400
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 100))
    except ValueError:
        g.log_fields["error"] = "invalid limit"
        return jsonify(error="'limit' must be an integer"), 400
    category = request.args.get("category")
    g.log_fields.update(limit=limit, category=category)

    pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    sql = (
        "SELECT id, category, model, created_at, narrative "
        "FROM tickets WHERE narrative ILIKE %s"
    )
    params = [pattern]
    if category:
        sql += " AND category = %s"
        params.append(category)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)

    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    for r in rows:
        r["created_at"] = r["created_at"].isoformat()
    g.log_fields["count"] = len(rows)
    return jsonify(query=q, count=len(rows), results=rows)


@app.get("/stats")
def stats():
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM tickets GROUP BY category"
        ).fetchall()
    counts = {c: 0 for c in CATEGORIES}
    counts.update({r["category"]: r["n"] for r in rows})
    g.log_fields["total"] = sum(counts.values())
    return jsonify(total=sum(counts.values()), by_category=counts)


@app.get("/health")
def health():
    return jsonify(status="ok", model=MODEL, prompt_version=PROMPT_VERSION, seed=SEED,
                   temperature=TEMPERATURE, think=THINK)
