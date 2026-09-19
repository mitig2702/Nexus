import threading
from collections import defaultdict

_lock = threading.Lock()
_requests_total = 0
_requests_by_path = defaultdict(int)
_agent_calls_total = 0
_tool_calls = defaultdict(int)
_tickets_created_total = 0
_tickets_auto_resolved_total = 0
_tickets_escalated_total = 0
_digests_sent_total = 0
_marketing_content_generated_total = 0


def record_request(path: str):
    global _requests_total
    with _lock:
        _requests_total += 1
        _requests_by_path[path] += 1


def record_agent_call():
    global _agent_calls_total
    with _lock:
        _agent_calls_total += 1


def record_tool_call(tool_name: str):
    with _lock:
        _tool_calls[tool_name] += 1


def _persist(name: str, value: int):
    """Best-effort write-through to Turso so ROI counters survive a process restart.
    Never let a metrics write break the feature that triggered it."""
    try:
        from db import execute
        execute(
            "INSERT INTO metrics_counters (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
            [name, value],
        )
    except Exception:
        pass


def load_persisted():
    """Hydrate in-memory ROI counters from Turso — call once at startup."""
    global _tickets_created_total, _tickets_auto_resolved_total, _tickets_escalated_total
    global _digests_sent_total, _marketing_content_generated_total
    try:
        from db import execute, rows_to_dicts
        values = {r["name"]: int(r["value"]) for r in rows_to_dicts(execute("SELECT name, value FROM metrics_counters"))}
    except Exception:
        return
    with _lock:
        _tickets_created_total = values.get("tickets_created", _tickets_created_total)
        _tickets_auto_resolved_total = values.get("tickets_auto_resolved", _tickets_auto_resolved_total)
        _tickets_escalated_total = values.get("tickets_escalated", _tickets_escalated_total)
        _digests_sent_total = values.get("digests_sent", _digests_sent_total)
        _marketing_content_generated_total = values.get("marketing_content_generated", _marketing_content_generated_total)


def record_ticket_created():
    global _tickets_created_total
    with _lock:
        _tickets_created_total += 1
        value = _tickets_created_total
    _persist("tickets_created", value)


def record_ticket_auto_resolved():
    global _tickets_auto_resolved_total
    with _lock:
        _tickets_auto_resolved_total += 1
        value = _tickets_auto_resolved_total
    _persist("tickets_auto_resolved", value)


def record_ticket_escalated():
    global _tickets_escalated_total
    with _lock:
        _tickets_escalated_total += 1
        value = _tickets_escalated_total
    _persist("tickets_escalated", value)


def record_digest_sent():
    global _digests_sent_total
    with _lock:
        _digests_sent_total += 1
        value = _digests_sent_total
    _persist("digests_sent", value)


def record_marketing_content_generated():
    global _marketing_content_generated_total
    with _lock:
        _marketing_content_generated_total += 1
        value = _marketing_content_generated_total
    _persist("marketing_content_generated", value)


def get_roi_stats(avg_minutes_saved_per_auto_resolve: int = 10) -> dict:
    with _lock:
        created = _tickets_created_total
        auto_resolved = _tickets_auto_resolved_total
        escalated = _tickets_escalated_total
        digests = _digests_sent_total
        marketing = _marketing_content_generated_total
    total_seen = created + auto_resolved
    deflection_rate = round(auto_resolved / total_seen, 3) if total_seen else 0.0
    return {
        "tickets_created": created,
        "tickets_auto_resolved_by_kb": auto_resolved,
        "tickets_escalated_sla_breach": escalated,
        "deflection_rate": deflection_rate,
        "estimated_minutes_saved": auto_resolved * avg_minutes_saved_per_auto_resolve,
        "digests_sent": digests,
        "marketing_content_generated": marketing,
    }


def render_prometheus() -> str:
    with _lock:
        lines = [
            "# HELP nexus_requests_total Total HTTP requests served",
            "# TYPE nexus_requests_total counter",
            f"nexus_requests_total {_requests_total}",
            "# HELP nexus_requests_by_path_total HTTP requests served per path",
            "# TYPE nexus_requests_by_path_total counter",
        ]
        for path, count in _requests_by_path.items():
            lines.append(f'nexus_requests_by_path_total{{path="{path}"}} {count}')
        lines += [
            "# HELP nexus_agent_calls_total Total /ask agent invocations",
            "# TYPE nexus_agent_calls_total counter",
            f"nexus_agent_calls_total {_agent_calls_total}",
            "# HELP nexus_tool_calls_total Agent tool invocations by tool name",
            "# TYPE nexus_tool_calls_total counter",
        ]
        for tool, count in _tool_calls.items():
            lines.append(f'nexus_tool_calls_total{{tool="{tool}"}} {count}')
        lines += [
            "# HELP nexus_tickets_created_total IT tickets actually created",
            "# TYPE nexus_tickets_created_total counter",
            f"nexus_tickets_created_total {_tickets_created_total}",
            "# HELP nexus_tickets_auto_resolved_total Tickets deflected by a KB match instead of being created",
            "# TYPE nexus_tickets_auto_resolved_total counter",
            f"nexus_tickets_auto_resolved_total {_tickets_auto_resolved_total}",
            "# HELP nexus_tickets_escalated_total Tickets auto-escalated for breaching SLA",
            "# TYPE nexus_tickets_escalated_total counter",
            f"nexus_tickets_escalated_total {_tickets_escalated_total}",
            "# HELP nexus_digests_sent_total Ops digests sent",
            "# TYPE nexus_digests_sent_total counter",
            f"nexus_digests_sent_total {_digests_sent_total}",
            "# HELP nexus_marketing_content_generated_total Marketing content drafts generated",
            "# TYPE nexus_marketing_content_generated_total counter",
            f"nexus_marketing_content_generated_total {_marketing_content_generated_total}",
        ]
    return "\n".join(lines) + "\n"
