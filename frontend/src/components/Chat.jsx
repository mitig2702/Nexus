import { useState, useRef, useEffect } from "react";
import { api } from "../api";

export default function Chat() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const sessionId = useRef(crypto.randomUUID());
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSubmit(e) {
    e.preventDefault();
    const question = input.trim();
    if (!question || loading) return;

    setMessages((m) => [...m, { role: "human", content: question }]);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const res = await api.ask(question, sessionId.current);
      sessionId.current = res.session_id;
      setMessages((m) => [
        ...m,
        {
          role: "assistant",
          content: res.answer,
          actionTaken: res.action_taken,
          sources: res.sources,
        },
      ]);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="chat">
      <div className="chat-messages">
        {messages.length === 0 && (
          <p className="chat-empty">
            Ask about leave policy, raise a ticket, look up a colleague, or pull a report.
          </p>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`chat-bubble chat-bubble--${m.role}`}>
            <div className="chat-bubble-content">{m.content}</div>
            {m.actionTaken && <div className="chat-bubble-meta">tool: {m.actionTaken}</div>}
            {m.sources?.length > 0 && (
              <div className="chat-bubble-meta">sources: {m.sources.join(", ")}</div>
            )}
          </div>
        ))}
        {loading && <div className="chat-bubble chat-bubble--assistant chat-bubble--loading">…</div>}
        <div ref={bottomRef} />
      </div>

      {error && <div className="chat-error">{error}</div>}

      <form className="chat-input" onSubmit={handleSubmit}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask Nexus anything…"
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  );
}
