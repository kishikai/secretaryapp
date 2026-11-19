import { useEffect, useState } from "react";

export default function App() {
  const [health, setHealth] = useState("checking...");
  const [messages, setMessages] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    Promise.all([fetch("/api/health").then((r) => r.json()), fetch("/api/messages").then((r) => r.json())])
      .then(([h, m]) => {
        setHealth(h.status);
        setMessages(m);
      })
      .catch((e) => {
        setHealth("unreachable");
        setError(e.message);
      });
  }, []);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", maxWidth: 720, margin: "2rem auto", padding: "0 1rem" }}>
      <h1>Secretary Messenger Monitor</h1>
      <p>
        Backend status: <strong>{health}</strong>
      </p>
      {error && <p role="alert">Could not reach the backend: {error}</p>}
      <h2>Recent messages</h2>
      {messages.length === 0 ? (
        <p>No messages yet.</p>
      ) : (
        <ul>
          {messages.map((m) => (
            <li key={m.id}>
              <small>
                [{m.source}] {m.chat_id ?? "no chat"} · {m.created_at}
              </small>
              <div>{m.message_text}</div>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
