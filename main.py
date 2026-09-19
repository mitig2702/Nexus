import os
import io
import csv
import json
import time
import uuid
import logging
import urllib.parse
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Dict, Any, List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from db import execute, rows_to_dicts, migrate as db_migrate, ping as db_ping
from models import MemberRequestCreate, HumanOverrideRequest
from tools import (
    CATEGORY_TEAMS,
    PRIORITY_WEIGHTS,
    log_request_event,
)
from agent import get_hearthline_agent
from seed import seed_database

logger = logging.getLogger("hearthline")
logging.basicConfig(level=logging.INFO)

PORT = int(os.getenv("PORT", "8000"))

# Initialize DB on startup
try:
    db_migrate()
    # Check if empty, seed automatically
    rows = rows_to_dicts(execute("SELECT COUNT(*) as count FROM member_requests"))
    if not rows or rows[0].get("count", 0) == 0:
        seed_database()
except Exception as e:
    logger.warning(f"DB initialization warning: {e}")


# ── Core Service Functions ──────────────────────────────────────────────────

def get_requests_list(
    status: Optional[str] = None,
    category: Optional[str] = None,
    priority: Optional[str] = None,
    tier: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    where = []
    params = []
    if status:
        where.append("status = ?")
        params.append(status)
    if category:
        where.append("category = ?")
        params.append(category)
    if priority:
        where.append("priority = ?")
        params.append(priority)
    if tier:
        where.append("requester_tier = ?")
        params.append(tier)
    if q:
        where.append("(requester_name LIKE ? OR body_text LIKE ? OR request_id LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])

    sql = "SELECT * FROM member_requests"
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Crucial: Urgent requests surface first (priority_score DESC, submitted_at DESC)
    sql += " ORDER BY priority_score DESC, submitted_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = rows_to_dicts(execute(sql, params))
    for r in rows:
        if r.get("actions_taken") and isinstance(r["actions_taken"], str):
            try:
                r["actions_taken"] = json.loads(r["actions_taken"])
            except Exception:
                pass
        if r.get("override_details") and isinstance(r["override_details"], str):
            try:
                r["override_details"] = json.loads(r["override_details"])
            except Exception:
                pass
    return rows


def get_request_detail(request_id: str) -> Optional[Dict[str, Any]]:
    rows = rows_to_dicts(execute("SELECT * FROM member_requests WHERE request_id = ?", [request_id]))
    if not rows:
        return None
    req = rows[0]
    if req.get("actions_taken") and isinstance(req["actions_taken"], str):
        try:
            req["actions_taken"] = json.loads(req["actions_taken"])
        except Exception:
            pass
    if req.get("override_details") and isinstance(req["override_details"], str):
        try:
            req["override_details"] = json.loads(req["override_details"])
        except Exception:
            pass

    # Fetch audit events
    events = rows_to_dicts(
        execute("SELECT * FROM request_events WHERE request_id = ? ORDER BY created_at ASC", [request_id])
    )
    for ev in events:
        if ev.get("data") and isinstance(ev["data"], str):
            try:
                ev["data"] = json.loads(ev["data"])
            except Exception:
                pass
    req["audit_trail"] = events
    return req


def ingest_member_request(data: Dict[str, Any]) -> Dict[str, Any]:
    requester_name = data.get("requester_name", "").strip()
    if not requester_name:
        raise ValueError("requester_name is required")
    body_text = data.get("body_text", "").strip()
    if not body_text:
        raise ValueError("body_text is required")

    request_id = data.get("request_id") or f"REQ-{uuid.uuid4().hex[:6].upper()}"
    requester_tier = data.get("requester_tier", "standard")
    submitted_at = data.get("submitted_at") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    now = datetime.now(timezone.utc).isoformat()

    execute(
        "INSERT INTO member_requests (request_id, requester_name, requester_tier, status, submitted_at, body_text, created_at, updated_at) "
        "VALUES (?, ?, ?, 'new', ?, ?, ?, ?)",
        [request_id, requester_name, requester_tier, submitted_at, body_text, now, now],
    )
    log_request_event(request_id, "ingested", f"Request submitted by {requester_name} ({requester_tier})", data)

    # Process with autonomous agent
    agent = get_hearthline_agent()
    process_result = agent.process_request(request_id)
    return get_request_detail(request_id) or process_result


def process_batch_pending() -> List[Dict[str, Any]]:
    rows = rows_to_dicts(execute("SELECT request_id FROM member_requests WHERE status = 'new' ORDER BY submitted_at ASC"))
    agent = get_hearthline_agent()
    results = []
    for r in rows:
        try:
            res = agent.process_request(r["request_id"])
            results.append(res)
        except Exception as e:
            results.append({"request_id": r["request_id"], "error": str(e)})
    return results


def apply_human_override(request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    rows = rows_to_dicts(execute("SELECT * FROM member_requests WHERE request_id = ?", [request_id]))
    if not rows:
        raise ValueError(f"Request '{request_id}' not found")
    req = rows[0]

    updates = {}
    prev_state = {
        "category": req.get("category"),
        "priority": req.get("priority"),
        "assigned_team": req.get("assigned_team"),
        "status": req.get("status"),
    }

    if payload.get("category"):
        updates["category"] = payload["category"]
        if not payload.get("assigned_team"):
            updates["assigned_team"] = CATEGORY_TEAMS.get(payload["category"], req.get("assigned_team"))
    if payload.get("priority"):
        updates["priority"] = payload["priority"]
        updates["priority_score"] = PRIORITY_WEIGHTS.get(payload["priority"], 2)
    if payload.get("assigned_team"):
        updates["assigned_team"] = payload["assigned_team"]
    if payload.get("status"):
        updates["status"] = payload["status"]

    override_record = {
        "reviewer_name": payload.get("reviewer_name") or "Member Success Lead",
        "override_note": payload.get("override_note") or "Human review override",
        "overridden_at": datetime.now(timezone.utc).isoformat(),
        "previous_state": prev_state,
        "new_state": updates,
    }

    set_clauses = ["human_override = 1", "override_details = ?", "updated_at = ?"]
    params = [json.dumps(override_record), datetime.now(timezone.utc).isoformat()]

    for k, v in updates.items():
        set_clauses.append(f"{k} = ?")
        params.append(v)

    params.append(request_id)
    execute(f"UPDATE member_requests SET {', '.join(set_clauses)} WHERE request_id = ?", params)

    log_request_event(
        request_id,
        "human_override",
        f"Overridden by {override_record['reviewer_name']}: {override_record['override_note']}",
        override_record,
    )

    return get_request_detail(request_id)


def get_dashboard_stats() -> Dict[str, Any]:
    all_reqs = rows_to_dicts(execute("SELECT status, priority, category, discrepancy_verified, human_override FROM member_requests"))
    total = len(all_reqs)
    urgent = sum(1 for r in all_reqs if r.get("priority") == "urgent")
    auto_closed = sum(1 for r in all_reqs if r.get("status") == "auto_closed")
    courtesy_resolutions = sum(1 for r in all_reqs if r.get("discrepancy_verified") == 1)
    overridden = sum(1 for r in all_reqs if r.get("human_override") == 1)

    categories = {}
    for r in all_reqs:
        c = r.get("category") or "Unclassified"
        categories[c] = categories.get(c, 0) + 1

    return {
        "total_requests": total,
        "urgent_requests": urgent,
        "auto_closed_duplicates": auto_closed,
        "courtesy_resolutions_issued": courtesy_resolutions,
        "human_overrides": overridden,
        "category_distribution": categories,
    }


# ── Interactive Web Dashboard HTML ──────────────────────────────────────────
HTML_PORTAL = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Hearthline Coworking — Member Request Operations</title>
  <style>
    :root {
      --bg: #0f172a;
      --card-bg: #1e293b;
      --card-border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #f59e0b;
      --primary-hover: #d97706;
      --accent: #38bdf8;
      --urgent: #ef4444;
      --high: #f97316;
      --medium: #3b82f6;
      --low: #64748b;
      --success: #10b981;
      --gold: #fbbf24;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 24px;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 24px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--card-border);
    }
    .brand h1 { font-size: 1.6rem; color: #fff; display: flex; align-items: center; gap: 10px; }
    .brand p { color: var(--text-muted); font-size: 0.9rem; margin-top: 4px; }
    .badge {
      display: inline-block;
      padding: 3px 8px;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .badge-urgent { background: rgba(239,68,68,0.2); color: #f87171; border: 1px solid #ef4444; }
    .badge-high { background: rgba(249,115,22,0.2); color: #fb923c; border: 1px solid #f97316; }
    .badge-medium { background: rgba(59,130,246,0.2); color: #60a5fa; border: 1px solid #3b82f6; }
    .badge-low { background: rgba(100,116,139,0.2); color: #94a3b8; border: 1px solid #64748b; }
    .badge-gold { background: rgba(251,191,36,0.2); color: #fde047; border: 1px solid var(--gold); }
    .badge-standard { background: rgba(148,163,184,0.15); color: #cbd5e1; border: 1px solid #475569; }
    .badge-category { background: rgba(56,189,248,0.15); color: #7dd3fc; border: 1px solid #0284c7; }
    .badge-resolution { background: rgba(16,185,129,0.2); color: #34d399; border: 1px solid #10b981; }

    .stats-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .stat-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      padding: 16px;
      text-align: center;
    }
    .stat-val { font-size: 1.8rem; font-weight: 700; color: #fff; }
    .stat-label { font-size: 0.8rem; color: var(--text-muted); margin-top: 4px; text-transform: uppercase; }

    .actions-bar {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-bottom: 20px;
      align-items: center;
    }
    button, .btn {
      background: var(--primary);
      color: #000;
      border: none;
      padding: 9px 16px;
      border-radius: 6px;
      font-weight: 600;
      cursor: pointer;
      font-size: 0.88rem;
      transition: background 0.15s;
    }
    button:hover, .btn:hover { background: var(--primary-hover); }
    .btn-secondary { background: #334155; color: #fff; }
    .btn-secondary:hover { background: #475569; }
    .btn-accent { background: var(--accent); color: #000; }
    .btn-accent:hover { background: #0284c7; color: #fff; }

    select, input[type="text"] {
      background: #1e293b;
      border: 1px solid var(--card-border);
      color: #fff;
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 0.88rem;
    }

    .table-container {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 8px;
      overflow-x: auto;
    }
    table { width: 100%; border-collapse: collapse; text-align: left; }
    th {
      background: #162032;
      padding: 12px 16px;
      font-size: 0.8rem;
      color: var(--text-muted);
      text-transform: uppercase;
      border-bottom: 1px solid var(--card-border);
    }
    td { padding: 14px 16px; border-bottom: 1px solid #243247; font-size: 0.9rem; vertical-align: middle; }
    tr:hover { background: rgba(255,255,255,0.02); }
    .urgent-row { background: rgba(239, 68, 68, 0.08); }

    .modal {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(0,0,0,0.75);
      align-items: center;
      justify-content: center;
      z-index: 999;
      padding: 20px;
    }
    .modal-content {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      width: 100%;
      max-width: 760px;
      max-height: 90vh;
      overflow-y: auto;
      padding: 24px;
      position: relative;
    }
    .close-btn {
      position: absolute;
      top: 16px;
      right: 16px;
      background: transparent;
      color: var(--text-muted);
      font-size: 1.5rem;
      cursor: pointer;
      border: none;
      padding: 4px;
    }
    .reasoning-box {
      background: #0b1120;
      border-left: 3px solid var(--accent);
      padding: 14px;
      border-radius: 4px;
      font-family: monospace;
      font-size: 0.85rem;
      white-space: pre-wrap;
      color: #e2e8f0;
      margin: 12px 0;
    }
    .resolution-alert {
      background: rgba(16,185,129,0.15);
      border: 1px solid var(--success);
      color: #34d399;
      padding: 12px;
      border-radius: 6px;
      margin: 12px 0;
      font-size: 0.9rem;
    }
    .form-group { margin-bottom: 14px; }
    .form-group label { display: block; font-size: 0.82rem; color: var(--text-muted); margin-bottom: 4px; text-transform: uppercase; }
    .form-group input, .form-group select, .form-group textarea {
      width: 100%;
      background: #0f172a;
      border: 1px solid var(--card-border);
      color: #fff;
      padding: 8px 12px;
      border-radius: 6px;
      font-size: 0.9rem;
    }
    .tabs { display: flex; gap: 8px; margin-bottom: 16px; border-bottom: 1px solid var(--card-border); padding-bottom: 8px; }
    .tab-btn { background: transparent; color: var(--text-muted); padding: 8px 16px; border-radius: 6px; }
    .tab-btn.active { background: #334155; color: #fff; }
  </style>
</head>
<body>

  <header class="header">
    <div class="brand">
      <h1>🏢 Hearthline Coworking <span class="badge badge-gold">Member Success Agent</span></h1>
      <p>Autonomous Ingestion, Multi-Step LLM Classification, Priority Surfacing & Billing Resolution</p>
    </div>
    <div>
      <button class="btn-secondary" onclick="seedData()">🔄 Reset / Seed Data</button>
      <button class="btn-accent" onclick="processBatch()">⚡ Run Agent on Pending</button>
      <button onclick="openIngestModal()">➕ New Request</button>
    </div>
  </header>

  <div class="stats-row" id="statsRow">
    <div class="stat-card"><div class="stat-val" id="statTotal">-</div><div class="stat-label">Total Requests</div></div>
    <div class="stat-card"><div class="stat-val" style="color:var(--urgent)" id="statUrgent">-</div><div class="stat-label">Urgent (Surfaced 1st)</div></div>
    <div class="stat-card"><div class="stat-val" style="color:var(--success)" id="statCourtesy">-</div><div class="stat-label">Courtesy Resolutions</div></div>
    <div class="stat-card"><div class="stat-val" style="color:var(--accent)" id="statAutoClosed">-</div><div class="stat-label">Auto-Closed Duplicates</div></div>
    <div class="stat-card"><div class="stat-val" style="color:var(--gold)" id="statOverrides">-</div><div class="stat-label">Human Overrides</div></div>
  </div>

  <div class="actions-bar">
    <select id="filterCategory" onchange="loadRequests()">
      <option value="">All Categories</option>
      <option value="Access & Badge">Access & Badge</option>
      <option value="Room Booking">Room Booking</option>
      <option value="Billing">Billing</option>
      <option value="Facility & Maintenance">Facility & Maintenance</option>
      <option value="Membership Change">Membership Change</option>
    </select>

    <select id="filterPriority" onchange="loadRequests()">
      <option value="">All Priorities</option>
      <option value="urgent">Urgent</option>
      <option value="high">High</option>
      <option value="medium">Medium</option>
      <option value="low">Low</option>
    </select>

    <select id="filterStatus" onchange="loadRequests()">
      <option value="">All Statuses</option>
      <option value="new">New</option>
      <option value="routed">Routed</option>
      <option value="auto_closed">Auto-Closed</option>
      <option value="resolved">Resolved</option>
    </select>

    <input type="text" id="searchBox" placeholder="Search member, ID, or text..." oninput="loadRequests()" style="min-width: 260px;" />
  </div>

  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>Priority</th>
          <th>Request ID</th>
          <th>Requester</th>
          <th>Tier</th>
          <th>Category</th>
          <th>Assigned Team</th>
          <th>Message</th>
          <th>Status / Action</th>
          <th>Review</th>
        </tr>
      </thead>
      <tbody id="requestsTbody">
        <tr><td colspan="9" style="text-align:center; padding: 30px;">Loading requests...</td></tr>
      </tbody>
    </table>
  </div>

  <!-- Review / Override Modal -->
  <div class="modal" id="reviewModal">
    <div class="modal-content">
      <button class="close-btn" onclick="closeModal('reviewModal')">&times;</button>
      <h2 id="modalTitle" style="margin-bottom: 12px;">Review Member Request</h2>
      <div id="modalBadges" style="display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap;"></div>

      <div style="background:#0f172a; padding:12px; border-radius:6px; margin-bottom:16px;">
        <strong style="color:var(--text-muted); font-size:0.8rem; text-transform:uppercase;">Member Body Text:</strong>
        <p id="modalBodyText" style="margin-top:6px; font-size:0.95rem;"></p>
      </div>

      <div id="modalCourtesyAlert" style="display:none;" class="resolution-alert"></div>

      <h4 style="color:var(--accent); margin-top:16px;">🧠 In-House LLM Reasoning Trace:</h4>
      <div class="reasoning-box" id="modalReasoning"></div>

      <h4 style="color:var(--primary); margin-top:16px;">⚙️ Autonomous Actions & Tools Executed:</h4>
      <div id="modalActions" style="display:flex; gap:6px; flex-wrap:wrap; margin:8px 0 16px 0;"></div>

      <h4 style="color:#fff; margin-top:16px; border-top:1px solid var(--card-border); padding-top:16px;">✍️ Human Review & Override:</h4>
      <form id="overrideForm" onsubmit="submitOverride(event)" style="margin-top:12px;">
        <input type="hidden" id="overrideRequestId" />
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
          <div class="form-group">
            <label>Category</label>
            <select id="overrideCategory">
              <option value="Access & Badge">Access & Badge</option>
              <option value="Room Booking">Room Booking</option>
              <option value="Billing">Billing</option>
              <option value="Facility & Maintenance">Facility & Maintenance</option>
              <option value="Membership Change">Membership Change</option>
            </select>
          </div>
          <div class="form-group">
            <label>Priority</label>
            <select id="overridePriority">
              <option value="urgent">Urgent</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>
        </div>

        <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px;">
          <div class="form-group">
            <label>Assigned Team</label>
            <select id="overrideTeam">
              <option value="Access & Security Team">Access & Security Team</option>
              <option value="Room & Event Operations Team">Room & Event Operations Team</option>
              <option value="Finance & Billing Team">Finance & Billing Team</option>
              <option value="Facilities & Maintenance Team">Facilities & Maintenance Team</option>
              <option value="Member Success & Accounts Team">Member Success & Accounts Team</option>
            </select>
          </div>
          <div class="form-group">
            <label>Status</label>
            <select id="overrideStatus">
              <option value="routed">Routed</option>
              <option value="open">Open</option>
              <option value="in_progress">In Progress</option>
              <option value="resolved">Resolved</option>
              <option value="closed">Closed</option>
            </select>
          </div>
        </div>

        <div class="form-group">
          <label>Reviewer Name</label>
          <input type="text" id="overrideReviewer" value="Sarah Miller (Member Success Lead)" required />
        </div>

        <div class="form-group">
          <label>Override Notes / Feedback</label>
          <textarea id="overrideNote" rows="2" placeholder="Explain rationale for this human override..." required></textarea>
        </div>

        <button type="submit" class="btn" style="width:100%;">Save Human Override</button>
      </form>
    </div>
  </div>

  <!-- Ingest New Request Modal -->
  <div class="modal" id="ingestModal">
    <div class="modal-content" style="max-width: 520px;">
      <button class="close-btn" onclick="closeModal('ingestModal')">&times;</button>
      <h2 style="margin-bottom: 16px;">Submit Member Request</h2>
      <form onsubmit="submitNewRequest(event)">
        <div class="form-group">
          <label>Member Name</label>
          <input type="text" id="newRequesterName" placeholder="e.g. Elena Rostova" required />
        </div>
        <div class="form-group">
          <label>Membership Tier</label>
          <select id="newRequesterTier">
            <option value="standard">Standard Member</option>
            <option value="founding member">Founding Member (VIP Priority)</option>
          </select>
        </div>
        <div class="form-group">
          <label>Request Details</label>
          <textarea id="newBodyText" rows="4" placeholder="Describe your inquiry, discrepancy, room booking, or facility issue..." required></textarea>
        </div>
        <button type="submit" class="btn" style="width:100%;">Ingest & Process with Agent</button>
      </form>
    </div>
  </div>

  <script>
    async function fetchAPI(endpoint, options = {}) {
      const res = await fetch(endpoint, options);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${res.status}`);
      }
      return res.json();
    }

    async function loadStats() {
      try {
        const s = await fetchAPI('/stats');
        document.getElementById('statTotal').innerText = s.total_requests;
        document.getElementById('statUrgent').innerText = s.urgent_requests;
        document.getElementById('statCourtesy').innerText = s.courtesy_resolutions_issued;
        document.getElementById('statAutoClosed').innerText = s.auto_closed_duplicates;
        document.getElementById('statOverrides').innerText = s.human_overrides;
      } catch (e) { console.error(e); }
    }

    async function loadRequests() {
      const cat = document.getElementById('filterCategory').value;
      const pri = document.getElementById('filterPriority').value;
      const st = document.getElementById('filterStatus').value;
      const q = document.getElementById('searchBox').value;

      const params = new URLSearchParams();
      if (cat) params.append('category', cat);
      if (pri) params.append('priority', pri);
      if (st) params.append('status', st);
      if (q) params.append('q', q);

      try {
        const list = await fetchAPI('/requests?' + params.toString());
        const tbody = document.getElementById('requestsTbody');
        if (list.length === 0) {
          tbody.innerHTML = '<tr><td colspan="9" style="text-align:center; padding: 24px; color:var(--text-muted)">No matching requests found.</td></tr>';
          return;
        }

        tbody.innerHTML = list.map(r => {
          const isUrgent = r.priority === 'urgent';
          const pBadge = `<span class="badge badge-${r.priority || 'low'}">${r.priority || 'unassigned'}</span>`;
          const tBadge = r.requester_tier === 'founding member' ? `<span class="badge badge-gold">Founding</span>` : `<span class="badge badge-standard">Standard</span>`;
          const cBadge = r.category ? `<span class="badge badge-category">${r.category}</span>` : `<span style="color:var(--text-muted)">Pending</span>`;

          let extraBadges = '';
          if (r.courtesy_resolution) extraBadges += `<br><span class="badge badge-resolution" style="margin-top:4px;">💵 Resolution Issued</span>`;
          if (r.is_duplicate) extraBadges += `<br><span class="badge badge-accent" style="margin-top:4px;">Consolidated Duplicate</span>`;
          if (r.human_override) extraBadges += `<br><span class="badge badge-gold" style="margin-top:4px;">Human Overridden</span>`;

          return `
            <tr class="${isUrgent ? 'urgent-row' : ''}">
              <td>${pBadge}</td>
              <td><strong>${r.request_id}</strong></td>
              <td>${r.requester_name}</td>
              <td>${tBadge}</td>
              <td>${cBadge}</td>
              <td style="color:#cbd5e1; font-size:0.85rem;">${r.assigned_team || '-'}</td>
              <td style="max-width:320px; font-size:0.85rem; color:#cbd5e1;">${r.body_text}</td>
              <td><strong>${r.status}</strong> ${extraBadges}</td>
              <td><button class="btn-secondary" style="padding:4px 10px; font-size:0.8rem;" onclick="viewRequest('${r.request_id}')">Review</button></td>
            </tr>
          `;
        }).join('');
      } catch (e) {
        document.getElementById('requestsTbody').innerHTML = `<tr><td colspan="9" style="color:red; text-align:center;">Error: ${e.message}</td></tr>`;
      }
    }

    async function viewRequest(id) {
      try {
        const r = await fetchAPI('/requests/' + id);
        document.getElementById('overrideRequestId').value = r.request_id;
        document.getElementById('modalTitle').innerText = `${r.request_id} — ${r.requester_name}`;

        const badgesEl = document.getElementById('modalBadges');
        badgesEl.innerHTML = `
          <span class="badge badge-${r.priority || 'medium'}">${r.priority || 'Medium'}</span>
          <span class="badge badge-gold">${r.requester_tier}</span>
          <span class="badge badge-category">${r.category || 'Unclassified'}</span>
          <span class="badge badge-standard">Team: ${r.assigned_team || 'Unassigned'}</span>
          ${r.human_override ? '<span class="badge badge-gold">Overridden</span>' : ''}
        `;

        document.getElementById('modalBodyText').innerText = r.body_text;

        const alertEl = document.getElementById('modalCourtesyAlert');
        if (r.courtesy_resolution) {
          alertEl.style.display = 'block';
          alertEl.innerHTML = `<strong>✨ Courtesy Resolution Applied:</strong><br>${r.courtesy_resolution}`;
        } else {
          alertEl.style.display = 'none';
        }

        document.getElementById('modalReasoning').innerText = r.llm_reasoning || 'No reasoning trace available.';

        const actionsEl = document.getElementById('modalActions');
        actionsEl.innerHTML = (r.actions_taken || []).map(a => `<span class="badge badge-category">🔧 ${a}</span>`).join(' ') || '<span>None</span>';

        if (r.category) document.getElementById('overrideCategory').value = r.category;
        if (r.priority) document.getElementById('overridePriority').value = r.priority;
        if (r.assigned_team) document.getElementById('overrideTeam').value = r.assigned_team;
        if (r.status) document.getElementById('overrideStatus').value = r.status;

        document.getElementById('reviewModal').style.display = 'flex';
      } catch (e) { alert(e.message); }
    }

    async function submitOverride(e) {
      e.preventDefault();
      const id = document.getElementById('overrideRequestId').value;
      const payload = {
        category: document.getElementById('overrideCategory').value,
        priority: document.getElementById('overridePriority').value,
        assigned_team: document.getElementById('overrideTeam').value,
        status: document.getElementById('overrideStatus').value,
        reviewer_name: document.getElementById('overrideReviewer').value,
        override_note: document.getElementById('overrideNote').value,
      };
      try {
        await fetchAPI(`/requests/${id}/override`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
        });
        closeModal('reviewModal');
        loadStats();
        loadRequests();
      } catch (e) { alert('Override failed: ' + e.message); }
    }

    async function submitNewRequest(e) {
      e.preventDefault();
      const payload = {
        requester_name: document.getElementById('newRequesterName').value,
        requester_tier: document.getElementById('newRequesterTier').value,
        body_text: document.getElementById('newBodyText').value,
      };
      try {
        const res = await fetchAPI('/requests/ingest', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
        });
        closeModal('ingestModal');
        document.getElementById('newBodyText').value = '';
        loadStats();
        loadRequests();
        viewRequest(res.request_id);
      } catch (e) { alert('Ingest failed: ' + e.message); }
    }

    async function processBatch() {
      try {
        const res = await fetchAPI('/requests/batch-process', { method: 'POST' });
        alert(`Processed ${res.length} pending requests through the Hearthline Agent.`);
        loadStats();
        loadRequests();
      } catch (e) { alert(e.message); }
    }

    async function seedData() {
      if (!confirm("Reload seed data from member_requests.csv and invoice.csv?")) return;
      try {
        const res = await fetchAPI('/seed', { method: 'POST' });
        alert(`Seeded successfully: ${res.requests} requests, ${res.invoices} invoices.`);
        loadStats();
        loadRequests();
      } catch (e) { alert(e.message); }
    }

    function openIngestModal() { document.getElementById('ingestModal').style.display = 'flex'; }
    function closeModal(id) { document.getElementById(id).style.display = 'none'; }

    // Init
    loadStats();
    loadRequests();
  </script>
</body>
</html>
"""


# ── Built-in Standard Library HTTP Server ───────────────────────────────────

class HearthlineRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: Any):
        content = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
        self.end_headers()
        self.wfile.write(content)

    def _send_html(self, html: str):
        content = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/portal"):
            return self._send_html(HTML_PORTAL)

        if path == "/health":
            return self._send_json(200, {"status": "ok", "service": "hearthline-member-success"})

        if path == "/stats":
            return self._send_json(200, get_dashboard_stats())

        if path == "/invoices":
            rows = rows_to_dicts(execute("SELECT * FROM invoices ORDER BY id ASC"))
            return self._send_json(200, rows)

        if path == "/requests":
            status = query.get("status", [None])[0]
            category = query.get("category", [None])[0]
            priority = query.get("priority", [None])[0]
            tier = query.get("tier", [None])[0]
            q = query.get("q", [None])[0]
            limit = int(query.get("limit", [100])[0])
            offset = int(query.get("offset", [0])[0])
            rows = get_requests_list(status, category, priority, tier, q, limit, offset)
            return self._send_json(200, rows)

        if path.startswith("/requests/"):
            req_id = path.split("/")[2]
            detail = get_request_detail(req_id)
            if not detail:
                return self._send_json(404, {"error": f"Request '{req_id}' not found"})
            return self._send_json(200, detail)

        return self._send_json(404, {"error": "Not Found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length > 0 else b"{}"

        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except Exception:
            payload = {}

        if path == "/seed":
            res = seed_database()
            return self._send_json(200, res)

        if path == "/requests/ingest":
            try:
                res = ingest_member_request(payload)
                return self._send_json(201, res)
            except Exception as e:
                return self._send_json(400, {"error": str(e)})

        if path == "/requests/batch-process":
            try:
                res = process_batch_pending()
                return self._send_json(200, res)
            except Exception as e:
                return self._send_json(500, {"error": str(e)})

        if path.startswith("/requests/") and path.endswith("/override"):
            req_id = path.split("/")[2]
            try:
                res = apply_human_override(req_id, payload)
                return self._send_json(200, res)
            except Exception as e:
                return self._send_json(400, {"error": str(e)})

        return self._send_json(404, {"error": "Not Found"})


# ── FastAPI Application (if FastAPI installed) ──────────────────────────────
app = None
try:
    from fastapi import FastAPI, HTTPException, Query, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(
        title="Hearthline Coworking — Member Request Operations",
        description="Autonomous Multi-Step Member Request Classification, Prioritization & Billing Courtesy Resolution",
        version="2.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse)
    async def portal_root():
        return HTMLResponse(content=HTML_PORTAL)

    @app.get("/portal", response_class=HTMLResponse)
    async def portal_page():
        return HTMLResponse(content=HTML_PORTAL)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "hearthline-member-success"}

    @app.get("/stats")
    async def stats():
        return get_dashboard_stats()

    @app.get("/invoices")
    async def list_invoices():
        return rows_to_dicts(execute("SELECT * FROM invoices ORDER BY id ASC"))

    @app.get("/requests")
    async def list_requests(
        status: Optional[str] = Query(None),
        category: Optional[str] = Query(None),
        priority: Optional[str] = Query(None),
        tier: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        return get_requests_list(status, category, priority, tier, q, limit, offset)

    @app.get("/requests/{request_id}")
    async def get_request(request_id: str):
        detail = get_request_detail(request_id)
        if not detail:
            raise HTTPException(status_code=404, detail={"error": f"Request '{request_id}' not found"})
        return detail

    @app.post("/requests/ingest", status_code=201)
    async def ingest_request(body: MemberRequestCreate):
        try:
            return ingest_member_request(body.model_dump())
        except Exception as e:
            raise HTTPException(status_code=400, detail={"error": str(e)})

    @app.post("/requests/batch-process")
    async def batch_process():
        return process_batch_pending()

    @app.post("/requests/{request_id}/override")
    async def override_request(request_id: str, body: HumanOverrideRequest):
        try:
            return apply_human_override(request_id, body.model_dump(exclude_unset=True))
        except Exception as e:
            raise HTTPException(status_code=400, detail={"error": str(e)})

    @app.post("/seed")
    async def seed():
        return seed_database()

except ImportError:
    pass


def run_server(port: int = PORT):
    server = HTTPServer(("0.0.0.0", port), HearthlineRequestHandler)
    print(f"\n=======================================================")
    print(f"🏢 Hearthline Coworking Member Request Operations")
    print(f"📡 Server running at http://localhost:{port}/")
    print(f"=======================================================\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    run_server()
