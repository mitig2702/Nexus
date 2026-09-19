import os
import sqlite3
from typing import Any

_URL = None
_TOKEN = None
_USE_SQLITE = False
_DB_PATH = os.getenv("SQLITE_DB_PATH", os.path.join(os.path.dirname(__file__), "hearthline.db"))


def _init():
    global _URL, _TOKEN, _USE_SQLITE
    url = os.getenv("TURSO_URL", "")
    token = os.getenv("TURSO_TOKEN", "")
    if url and token:
        _URL = url.replace("libsql://", "https://", 1)
        _TOKEN = token
        _USE_SQLITE = False
    else:
        _USE_SQLITE = True


def _get_sqlite_conn():
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def execute(sql: str, args: list[Any] = None) -> dict:
    if _URL is None and not _USE_SQLITE:
        _init()

    if _USE_SQLITE:
        conn = _get_sqlite_conn()
        try:
            cur = conn.cursor()
            cur.execute(sql, args or [])
            conn.commit()
            if cur.description:
                cols = [{"name": d[0]} for d in cur.description]
                raw_rows = cur.fetchall()
                rows = []
                for r in raw_rows:
                    rows.append([{"value": r[i]} for i in range(len(cols))])
                return {"cols": cols, "rows": rows}
            return {"cols": [], "rows": []}
        finally:
            conn.close()

    # Turso HTTP API
    import requests

    def _wrap(v):
        if v is None:
            return {"type": "null"}
        if isinstance(v, int):
            return {"type": "integer", "value": str(v)}
        if isinstance(v, float):
            return {"type": "float", "value": str(v)}
        return {"type": "text", "value": str(v)}

    payload = {
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": sql,
                    "args": [_wrap(a) for a in (args or [])],
                },
            },
            {"type": "close"},
        ]
    }
    resp = requests.post(
        f"{_URL}/v2/pipeline",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()["results"][0]
    if result["type"] == "error":
        raise RuntimeError(result["error"]["message"])
    return result["response"]["result"]


MIGRATIONS = [
    # Legacy tables for backwards compatibility
    """CREATE TABLE IF NOT EXISTS employees (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        department TEXT,
        role TEXT,
        manager TEXT,
        leave_balance INTEGER,
        email TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS tickets (
        id TEXT PRIMARY KEY,
        title TEXT,
        description TEXT,
        priority TEXT,
        status TEXT,
        created_at TEXT,
        department TEXT,
        resolved_by_kb INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS reports (
        id TEXT PRIMARY KEY,
        type TEXT,
        period TEXT,
        summary TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ticket_events (
        id TEXT PRIMARY KEY,
        ticket_id TEXT NOT NULL,
        status TEXT NOT NULL,
        note TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ticket_comments (
        id TEXT PRIMARY KEY,
        ticket_id TEXT NOT NULL,
        author TEXT NOT NULL,
        comment TEXT NOT NULL,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS leave_requests (
        id TEXT PRIMARY KEY,
        employee_id TEXT NOT NULL,
        employee_name TEXT,
        days INTEGER NOT NULL,
        status TEXT NOT NULL,
        requested_at TEXT,
        decided_at TEXT,
        decided_by TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS metrics_counters (
        name TEXT PRIMARY KEY,
        value INTEGER DEFAULT 0
    )""",
    # Hearthline Coworking tables
    """CREATE TABLE IF NOT EXISTS member_requests (
        request_id TEXT PRIMARY KEY,
        requester_name TEXT NOT NULL,
        requester_tier TEXT NOT NULL,
        status TEXT NOT NULL,
        submitted_at TEXT NOT NULL,
        body_text TEXT NOT NULL,
        category TEXT,
        confidence REAL,
        priority TEXT,
        priority_score INTEGER DEFAULT 2,
        assigned_team TEXT,
        llm_reasoning TEXT,
        actions_taken TEXT,
        is_duplicate INTEGER DEFAULT 0,
        duplicate_of_id TEXT,
        discrepancy_checked INTEGER DEFAULT 0,
        discrepancy_verified INTEGER DEFAULT 0,
        discrepancy_details TEXT,
        courtesy_resolution TEXT,
        acknowledgment_message TEXT,
        human_override INTEGER DEFAULT 0,
        override_details TEXT,
        created_at TEXT,
        updated_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        requester_name TEXT NOT NULL,
        invoice_number TEXT,
        amount REAL,
        verified_discrepancy TEXT NOT NULL,
        billing_date TEXT,
        status TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS members (
        requester_name TEXT PRIMARY KEY,
        requester_tier TEXT,
        location TEXT,
        plan_type TEXT,
        active_badge_id TEXT,
        assigned_space TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS request_events (
        id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        note TEXT,
        data TEXT,
        created_at TEXT
    )""",
]


def migrate():
    """Idempotent schema catch-up for database."""
    for stmt in MIGRATIONS:
        try:
            execute(stmt)
        except Exception as e:
            err = str(e).lower()
            if "duplicate column" not in err and "already exists" not in err:
                raise


def ping() -> dict:
    try:
        execute("SELECT 1")
        return {"status": "up", "backend": "sqlite" if _USE_SQLITE else "turso"}
    except Exception as e:
        return {"status": "down", "error": str(e)}


def rows_to_dicts(result: dict) -> list[dict]:
    cols = [c["name"] for c in result.get("cols", [])]
    out = []
    for row in result.get("rows", []):
        out.append(dict(zip(cols, [v.get("value") for v in row])))
    return out
