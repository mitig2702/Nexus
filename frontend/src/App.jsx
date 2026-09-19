import { useState, useEffect, useCallback } from "react";
import { api } from "./api";
import "./App.css";

const CATEGORIES = [
  "Access & Badge",
  "Room Booking",
  "Billing",
  "Facility & Maintenance",
  "Membership Change",
];

const TEAMS = [
  "Access & Security Team",
  "Room & Event Operations Team",
  "Finance & Billing Team",
  "Facilities & Maintenance Team",
  "Member Success & Accounts Team",
];

const PRIORITIES = ["urgent", "high", "medium", "low"];
const STATUSES = ["new", "routed", "open", "in_progress", "auto_closed", "resolved"];

export default function App() {
  const [tab, setTab] = useState("queue");
  const [requests, setRequests] = useState([]);
  const [invoices, setInvoices] = useState([]);
  const [stats, setStats] = useState(null);
  const [selectedReq, setSelectedReq] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  // Filters
  const [catFilter, setCatFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [search, setSearch] = useState("");

  // Ingest form
  const [newName, setNewName] = useState("");
  const [newTier, setNewTier] = useState("standard");
  const [newBody, setNewBody] = useState("");

  // Override form
  const [overrideCat, setOverrideCat] = useState("");
  const [overridePriority, setOverridePriority] = useState("");
  const [overrideTeam, setOverrideTeam] = useState("");
  const [overrideStatus, setOverrideStatus] = useState("");
  const [overrideReviewer, setOverrideReviewer] = useState("Sarah Miller (Member Success Lead)");
  const [overrideNote, setOverrideNote] = useState("");

  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const params = {};
      if (catFilter) params.category = catFilter;
      if (priorityFilter) params.priority = priorityFilter;
      if (statusFilter) params.status = statusFilter;
      if (search) params.q = search;

      const [reqData, statsData] = await Promise.all([
        api.getRequests(params),
        api.getStats().catch(() => null),
      ]);
      setRequests(reqData);
      setStats(statsData);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [catFilter, priorityFilter, statusFilter, search]);

  const loadInvoices = useCallback(async () => {
    try {
      const invData = await api.getInvoices();
      setInvoices(invData);
    } catch (err) {
      setError(err.message);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  useEffect(() => {
    if (tab === "invoices") loadInvoices();
  }, [tab, loadInvoices]);

  async function openReviewModal(id) {
    try {
      const detail = await api.getRequest(id);
      setSelectedReq(detail);
      setOverrideCat(detail.category || "Facility & Maintenance");
      setOverridePriority(detail.priority || "medium");
      setOverrideTeam(detail.assigned_team || TEAMS[0]);
      setOverrideStatus(detail.status || "routed");
      setOverrideNote("");
    } catch (err) {
      alert("Failed to load request: " + err.message);
    }
  }

  async function handleOverrideSubmit(e) {
    e.preventDefault();
    if (!selectedReq) return;
    try {
      await api.overrideRequest(selectedReq.request_id, {
        category: overrideCat,
        priority: overridePriority,
        assigned_team: overrideTeam,
        status: overrideStatus,
        reviewer_name: overrideReviewer,
        override_note: overrideNote,
      });
      setSelectedReq(null);
      loadData();
    } catch (err) {
      alert("Override error: " + err.message);
    }
  }

  async function handleIngestSubmit(e) {
    e.preventDefault();
    try {
      const res = await api.ingestRequest({
        requester_name: newName,
        requester_tier: newTier,
        body_text: newBody,
      });
      setNewName("");
      setNewBody("");
      setTab("queue");
      loadData();
      openReviewModal(res.request_id);
    } catch (err) {
      alert("Ingestion error: " + err.message);
    }
  }

  async function handleBatchProcess() {
    try {
      const res = await api.batchProcess();
      alert(`Batch processed ${res.length} requests through autonomous agent.`);
      loadData();
    } catch (err) {
      alert("Batch error: " + err.message);
    }
  }

  async function handleSeed() {
    if (!confirm("Reset and seed database from member_requests.csv & invoice.csv?")) return;
    try {
      const res = await api.seedData();
      alert(`Seeded ${res.requests} requests and ${res.invoices} invoices.`);
      loadData();
    } catch (err) {
      alert("Seed error: " + err.message);
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand-header">
          <h1>🏢 Hearthline Coworking</h1>
          <p className="app-subtitle">Autonomous Member Request Agent, Prioritization & Billing Courtesy Resolution</p>
        </div>
        <div className="header-actions">
          <button className="btn-secondary" onClick={handleSeed}>🔄 Reset Seed Data</button>
          <button className="btn-accent" onClick={handleBatchProcess}>⚡ Batch Run Agent</button>
          <button className="btn-primary" onClick={() => setTab("ingest")}>➕ Submit Request</button>
        </div>
      </header>

      {stats && (
        <div className="stats-row">
          <div className="stat-card">
            <div className="stat-val">{stats.total_requests}</div>
            <div className="stat-label">Total Requests</div>
          </div>
          <div className="stat-card">
            <div className="stat-val text-urgent">{stats.urgent_requests}</div>
            <div className="stat-label">Urgent (Surfaced 1st)</div>
          </div>
          <div className="stat-card">
            <div className="stat-val text-success">{stats.courtesy_resolutions_issued}</div>
            <div className="stat-label">Courtesy Resolutions</div>
          </div>
          <div className="stat-card">
            <div className="stat-val text-accent">{stats.auto_closed_duplicates}</div>
            <div className="stat-label">Auto-Closed Duplicates</div>
          </div>
          <div className="stat-card">
            <div className="stat-val text-gold">{stats.human_overrides}</div>
            <div className="stat-label">Human Overrides</div>
          </div>
        </div>
      )}

      <nav className="tabs">
        <button className={`tab ${tab === "queue" ? "tab--active" : ""}`} onClick={() => setTab("queue")}>
          📋 Member Requests Queue
        </button>
        <button className={`tab ${tab === "invoices" ? "tab--active" : ""}`} onClick={() => setTab("invoices")}>
          💳 Invoices & Discrepancies
        </button>
        <button className={`tab ${tab === "ingest" ? "tab--active" : ""}`} onClick={() => setTab("ingest")}>
          ✍️ Submit New Request
        </button>
      </nav>

      {error && <div className="chat-error">{error}</div>}

      <main className="app-main">
        {tab === "queue" && (
          <div className="panel">
            <div className="filter-bar">
              <select value={catFilter} onChange={(e) => setCatFilter(e.target.value)}>
                <option value="">All Categories</option>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>

              <select value={priorityFilter} onChange={(e) => setPriorityFilter(e.target.value)}>
                <option value="">All Priorities</option>
                {PRIORITIES.map((p) => (
                  <option key={p} value={p}>{p.toUpperCase()}</option>
                ))}
              </select>

              <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                <option value="">All Statuses</option>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>

              <input
                type="text"
                placeholder="Search member, ID, or text..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                style={{ flex: 1, minWidth: "200px" }}
              />
            </div>

            <div className="requests-table-container">
              <table className="requests-table">
                <thead>
                  <tr>
                    <th>Priority</th>
                    <th>ID</th>
                    <th>Requester</th>
                    <th>Tier</th>
                    <th>Category</th>
                    <th>Assigned Team</th>
                    <th>Message</th>
                    <th>Status</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {requests.map((r) => {
                    const isUrgent = r.priority === "urgent";
                    return (
                      <tr key={r.request_id} className={isUrgent ? "row-urgent" : ""}>
                        <td><span className={`badge badge--${r.priority || "low"}`}>{r.priority || "unassigned"}</span></td>
                        <td><strong>{r.request_id}</strong></td>
                        <td>{r.requester_name}</td>
                        <td>
                          <span className={`badge ${r.requester_tier === "founding member" ? "badge--gold" : "badge--standard"}`}>
                            {r.requester_tier}
                          </span>
                        </td>
                        <td><span className="badge badge--category">{r.category || "Pending"}</span></td>
                        <td>{r.assigned_team || "-"}</td>
                        <td className="message-cell">{r.body_text}</td>
                        <td>
                          <strong>{r.status}</strong>
                          {r.courtesy_resolution && <div className="badge badge--resolution">💵 Courtesy Resolution</div>}
                          {r.is_duplicate ? <div className="badge badge--accent">Consolidated Dup</div> : null}
                          {r.human_override ? <div className="badge badge--gold">Overridden</div> : null}
                        </td>
                        <td>
                          <button className="btn-secondary btn-sm" onClick={() => openReviewModal(r.request_id)}>
                            Review
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                  {requests.length === 0 && !loading && (
                    <tr><td colSpan="9" style={{ textAlign: "center", padding: "20px" }}>No requests match filters.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === "invoices" && (
          <div className="panel">
            <h3>💳 Hearthline Member Invoices & Verified Discrepancies</h3>
            <p className="app-subtitle" style={{ marginBottom: "16px" }}>
              Ground-truth billing ledger used by the Autonomous Agent to verify discrepancies and issue courtesy resolutions.
            </p>
            <table className="requests-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Member Name</th>
                  <th>Invoice #</th>
                  <th>Amount</th>
                  <th>Verified Discrepancy Note</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {invoices.map((inv) => (
                  <tr key={inv.id || inv.requester_name}>
                    <td>#{inv.id}</td>
                    <td><strong>{inv.requester_name}</strong></td>
                    <td>{inv.invoice_number || "INV-2026-01"}</td>
                    <td>${inv.amount ? inv.amount.toFixed(2) : "0.00"}</td>
                    <td>
                      {inv.verified_discrepancy && inv.verified_discrepancy !== "None" ? (
                        <span style={{ color: "#34d399", fontWeight: 600 }}>{inv.verified_discrepancy}</span>
                      ) : (
                        <span style={{ color: "#94a3b8" }}>None</span>
                      )}
                    </td>
                    <td><span className="badge badge--standard">{inv.status || "verified"}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === "ingest" && (
          <div className="panel" style={{ maxWidth: "600px", margin: "0 auto" }}>
            <h3>✍️ Ingest New Member Request</h3>
            <p className="app-subtitle" style={{ marginBottom: "20px" }}>
              Submit a request to trigger the Hearthline Autonomous Agent: classification, history check, priority calculation, routing, and courtesy resolution.
            </p>
            <form onSubmit={handleIngestSubmit} className="ingest-form">
              <div className="form-group">
                <label>Member Name</label>
                <input
                  type="text"
                  placeholder="e.g. Elena Rostova"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label>Membership Tier</label>
                <select value={newTier} onChange={(e) => setNewTier(e.target.value)}>
                  <option value="standard">Standard Member</option>
                  <option value="founding member">Founding Member (VIP Priority)</option>
                </select>
              </div>

              <div className="form-group">
                <label>Body Text / Inquiry</label>
                <textarea
                  rows="4"
                  placeholder="e.g. I was overcharged $34 on my monthly desk billing..."
                  value={newBody}
                  onChange={(e) => setNewBody(e.target.value)}
                  required
                ></textarea>
              </div>

              <button type="submit" className="btn-primary" style={{ width: "100%", marginTop: "10px" }}>
                Ingest & Run Autonomous Agent
              </button>
            </form>
          </div>
        )}
      </main>

      {/* Human Review & Override Modal */}
      {selectedReq && (
        <div className="modal-backdrop">
          <div className="modal-dialog">
            <button className="modal-close" onClick={() => setSelectedReq(null)}>&times;</button>
            <h2>Review Request: {selectedReq.request_id}</h2>
            <div className="modal-meta-row">
              <span className={`badge badge--${selectedReq.priority || "medium"}`}>{selectedReq.priority || "Medium"}</span>
              <span className="badge badge--gold">{selectedReq.requester_tier}</span>
              <span className="badge badge--category">{selectedReq.category || "Unclassified"}</span>
              <span className="badge badge--standard">{selectedReq.assigned_team}</span>
            </div>

            <div className="modal-box">
              <label>Member Message ({selectedReq.requester_name}):</label>
              <p style={{ marginTop: "4px" }}>{selectedReq.body_text}</p>
            </div>

            {selectedReq.courtesy_resolution && (
              <div className="resolution-alert">
                <strong>✨ Autonomous Courtesy Resolution Issued:</strong>
                <div>{selectedReq.courtesy_resolution}</div>
              </div>
            )}

            <h4>🧠 In-House LLM Reasoning Trace:</h4>
            <div className="reasoning-trace">
              {selectedReq.llm_reasoning || "No reasoning trace available."}
            </div>

            <h4>⚙️ Tools & Actions Executed:</h4>
            <div className="actions-list">
              {(selectedReq.actions_taken || []).map((a, i) => (
                <span key={i} className="badge badge--category">🔧 {a}</span>
              ))}
            </div>

            <hr style={{ borderColor: "var(--card-border)", margin: "20px 0" }} />

            <h4>✍️ Human Review & Override:</h4>
            <form onSubmit={handleOverrideSubmit} className="override-form">
              <div className="form-row">
                <div className="form-group">
                  <label>Override Category</label>
                  <select value={overrideCat} onChange={(e) => setOverrideCat(e.target.value)}>
                    {CATEGORIES.map((c) => (
                      <option key={c} value={c}>{c}</option>
                    ))}
                  </select>
                </div>
                <div className="form-group">
                  <label>Override Priority</label>
                  <select value={overridePriority} onChange={(e) => setOverridePriority(e.target.value)}>
                    {PRIORITIES.map((p) => (
                      <option key={p} value={p}>{p.toUpperCase()}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label>Override Assigned Team</label>
                  <select value={overrideTeam} onChange={(e) => setOverrideTeam(e.target.value)}>
                    {TEAMS.map((t) => (
                      <option key={t} value={t}>{t}</option>
                    ))}
                  </select>
                </div>
                <div className="form-group">
                  <label>Override Status</label>
                  <select value={overrideStatus} onChange={(e) => setOverrideStatus(e.target.value)}>
                    {STATUSES.map((s) => (
                      <option key={s} value={s}>{s}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="form-group">
                <label>Reviewer Name</label>
                <input
                  type="text"
                  value={overrideReviewer}
                  onChange={(e) => setOverrideReviewer(e.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label>Override Rationale / Notes</label>
                <textarea
                  rows="2"
                  value={overrideNote}
                  onChange={(e) => setOverrideNote(e.target.value)}
                  placeholder="Explain why you are overriding this classification/routing..."
                  required
                ></textarea>
              </div>

              <button type="submit" className="btn-primary" style={{ width: "100%" }}>
                Save Human Override
              </button>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
