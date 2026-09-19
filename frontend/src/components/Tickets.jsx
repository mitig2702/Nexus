import { useState, useEffect, useCallback } from "react";
import { api } from "../api";

const STATUSES = ["open", "in_progress", "closed"];

function TicketComments({ ticketId }) {
  const [comments, setComments] = useState([]);
  const [author, setAuthor] = useState("");
  const [comment, setComment] = useState("");
  const [error, setError] = useState(null);

  const load = useCallback(() => {
    api.getTicketComments(ticketId).then(setComments).catch((err) => setError(err.message));
  }, [ticketId]);

  useEffect(load, [load]);

  async function handleAdd(e) {
    e.preventDefault();
    if (!author.trim() || !comment.trim()) return;
    try {
      await api.addTicketComment(ticketId, author.trim(), comment.trim());
      setComment("");
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="ticket-comments">
      {error && <div className="chat-error">{error}</div>}
      {comments.length === 0 && <p className="chat-empty">No comments yet.</p>}
      {comments.map((c) => (
        <div key={c.id} className="ticket-comment">
          <strong>{c.author}</strong> — {c.comment}
          <div className="chat-bubble-meta">{c.created_at}</div>
        </div>
      ))}
      <form className="ticket-comment-form" onSubmit={handleAdd}>
        <input placeholder="Your name" value={author} onChange={(e) => setAuthor(e.target.value)} />
        <input placeholder="Add a comment…" value={comment} onChange={(e) => setComment(e.target.value)} />
        <button type="submit">Add</button>
      </form>
    </div>
  );
}

export default function Tickets() {
  const [tickets, setTickets] = useState([]);
  const [statusFilter, setStatusFilter] = useState("");
  const [expanded, setExpanded] = useState(null);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const params = statusFilter ? { status: statusFilter } : {};
      setTickets(await api.listTickets(params));
    } catch (err) {
      setError(err.message);
    }
  }, [statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleStatusChange(id, status) {
    try {
      await api.updateTicketStatus(id, status);
      load();
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div className="panel">
      <p className="chat-empty">To create a ticket, type your request in the Chat tab.</p>
      <div className="panel-controls">
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <a className="export-link" href={`${import.meta.env.VITE_API_URL || "http://localhost:8000"}/tickets/export`}>
          Export CSV
        </a>
      </div>

      {error && <div className="chat-error">{error}</div>}

      <div className="ticket-board">
        {tickets.map((t) => (
          <div key={t.id} className={`ticket-card ticket-card--${t.priority}`}>
            <div className="ticket-card-header">
              <span className="ticket-id">{t.id}</span>
              <span className={`badge badge--${t.priority}`}>{t.priority}</span>
            </div>
            <div className="ticket-title">{t.title}</div>
            <div className="ticket-desc">{t.description}</div>
            <div className="ticket-card-footer">
              <select value={t.status} onChange={(e) => handleStatusChange(t.id, e.target.value)}>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <button onClick={() => setExpanded(expanded === t.id ? null : t.id)}>
                {expanded === t.id ? "Hide comments" : "Comments"}
              </button>
            </div>
            {expanded === t.id && <TicketComments ticketId={t.id} />}
          </div>
        ))}
        {tickets.length === 0 && <p className="chat-empty">No tickets found.</p>}
      </div>
    </div>
  );
}
