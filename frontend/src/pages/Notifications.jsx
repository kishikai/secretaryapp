import { useCallback, useEffect, useRef, useState } from "react";
import { api, isAbort, query } from "../api.js";
import { chatLabel, formatTime, plural } from "../format.js";

const PAGE = 30;

export default function Notifications({ onUnreadChange }) {
  const [unreadOnly, setUnreadOnly] = useState(true);
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const gen = useRef(0);

  const fetchPage = useCallback(
    (beforeId, signal) =>
      api(`/notifications${query({ unread: unreadOnly ? "true" : "", limit: PAGE, before_id: beforeId })}`, { signal }),
    [unreadOnly],
  );

  const reload = useCallback(async () => {
    const my = ++gen.current;
    setLoading(true);
    setError("");
    try {
      const page = await fetchPage(null);
      if (my !== gen.current) return;
      setItems(page);
      setHasMore(page.length === PAGE);
    } catch (e) {
      if (my === gen.current && !isAbort(e)) setError(e.message);
    } finally {
      if (my === gen.current) setLoading(false);
    }
  }, [fetchPage]);

  useEffect(() => {
    reload();
    return () => {
      gen.current++;
    };
  }, [reload]);

  const loadOlder = async () => {
    const my = gen.current;
    setBusy(true);
    try {
      const page = await fetchPage(items[items.length - 1].id);
      if (my !== gen.current) return;
      setItems((cur) => [...cur, ...page]);
      setHasMore(page.length === PAGE);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const markRead = async (n) => {
    setError("");
    try {
      await api(`/notifications/${n.id}/read`, { method: "POST" });
      setItems((cur) =>
        unreadOnly ? cur.filter((x) => x.id !== n.id) : cur.map((x) => (x.id === n.id ? { ...x, read_at: new Date().toISOString() } : x)),
      );
      setStatus("Marked 1 notification as read.");
      onUnreadChange?.();
    } catch (e) {
      setError(e.message);
    }
  };

  const markAll = async () => {
    setError("");
    setBusy(true);
    try {
      const r = await api("/notifications/read-all", { method: "POST" });
      setStatus(`Marked ${plural(r?.marked ?? 0, "notification", "notifications")} as read.`);
      onUnreadChange?.();
      await reload();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const hasUnread = items.some((n) => !n.read_at);

  return (
    <>
      <h1>Notifications</h1>
      <div className="toolbar">
        <div className="segmented" role="group" aria-label="Show">
          <button type="button" aria-pressed={unreadOnly} onClick={() => setUnreadOnly(true)}>
            Unread
          </button>
          <button type="button" aria-pressed={!unreadOnly} onClick={() => setUnreadOnly(false)}>
            All
          </button>
        </div>
        <button type="button" onClick={markAll} disabled={busy || !hasUnread}>
          Mark all read
        </button>
      </div>
      <p role="status" className="muted status-msg">
        {status}
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading && <p className="muted">Loading…</p>}
      {!loading && !error && items.length === 0 && (
        <p className="empty">{unreadOnly ? "You are all caught up. No unread notifications." : "No notifications yet. They appear when a rule with notifications on matches a message."}</p>
      )}
      <ul className="list">
        {items.map((n) => (
          <li key={n.id} className={`card item${n.read_at ? " is-read" : ""}`}>
            <div className="item-head">
              <strong>{n.rule_name}</strong>
              {n.matched_text && <mark className="matched">{n.matched_text}</mark>}
              {!n.read_at && <span className="chip accent">Unread</span>}
            </div>
            <p className="text clamp">{n.message_text}</p>
            <div className="meta">
              <span>{chatLabel(n.chat_id)}</span>
              <span>{formatTime(n.created_at)}</span>
              {!n.read_at && (
                <button type="button" className="small" onClick={() => markRead(n)}>
                  Mark read<span className="sr-only"> ({n.rule_name})</span>
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
      {hasMore && !loading && (
        <button type="button" onClick={loadOlder} disabled={busy}>
          {busy ? "Loading…" : "Load older"}
        </button>
      )}
    </>
  );
}
