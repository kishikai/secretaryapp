import { useCallback, useEffect, useId, useState } from "react";
import { api } from "../api.js";
import { chatLabel, formatTime, plural } from "../format.js";

const PATTERNS = ["url", "email", "phone", "money", "time", "date"];

function RuleRow({ rule, onChange, onDeleted }) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  const run = async (fn) => {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      await fn();
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const toggle = (field) =>
    run(async () => {
      const value = !rule[field];
      await api(`/rules/${rule.id}`, { method: "PATCH", body: { [field]: value } });
      onChange({ ...rule, [field]: value });
    });

  const backfill = () =>
    run(async () => {
      const r = await api(`/rules/${rule.id}/backfill`, { method: "POST" });
      setMsg(`Tagged ${plural(r.matching_messages, "message", "messages")}`);
    });

  const remove = () =>
    run(async () => {
      await api(`/rules/${rule.id}`, { method: "DELETE" });
      onDeleted(rule.id);
    });

  return (
    <li className={`card item${rule.enabled ? "" : " is-read"}`}>
      <div className="item-head">
        <strong>{rule.name}</strong>
        <span className="chip">{rule.kind === "keyword" ? "Keywords" : "Pattern"}</span>
        {rule.category && <span className="chip accent">{rule.category}</span>}
      </div>
      <p className="text">
        {rule.kind === "keyword" ? (rule.keywords ?? []).join(", ") : rule.pattern}
      </p>
      <div className="meta">
        <span>{rule.chat_id ? `Chat: ${chatLabel(rule.chat_id)}` : "All chats"}</span>
        <span>Created {formatTime(rule.created_at)}</span>
      </div>
      <div className="row-actions">
        <label className="check">
          <input type="checkbox" checked={rule.enabled} disabled={busy} onChange={() => toggle("enabled")} />
          Enabled<span className="sr-only"> ({rule.name})</span>
        </label>
        <label className="check">
          <input type="checkbox" checked={rule.notify} disabled={busy} onChange={() => toggle("notify")} />
          Notify<span className="sr-only"> ({rule.name})</span>
        </label>
        <button type="button" className="small" onClick={backfill} disabled={busy}>
          Backfill<span className="sr-only"> ({rule.name})</span>
        </button>
        {confirming ? (
          <span className="confirm">
            <button type="button" className="small danger" onClick={remove} disabled={busy}>
              Confirm delete<span className="sr-only"> ({rule.name})</span>
            </button>
            <button type="button" className="small" onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </span>
        ) : (
          <button type="button" className="small" onClick={() => setConfirming(true)} disabled={busy}>
            Delete<span className="sr-only"> ({rule.name})</span>
          </button>
        )}
      </div>
      <div role="status" className="muted">
        {msg}
      </div>
      {err && (
        <p role="alert" className="error">
          {err}
        </p>
      )}
    </li>
  );
}

function RuleForm({ chats, onCreated }) {
  const id = useId();
  const [name, setName] = useState("");
  const [kind, setKind] = useState("keyword");
  const [keywords, setKeywords] = useState("");
  const [pattern, setPattern] = useState(PATTERNS[0]);
  const [category, setCategory] = useState("");
  const [chat, setChat] = useState("");
  // Off by default, like the API: a broad rule (e.g. the time pattern) would otherwise notify on most messages.
  const [notify, setNotify] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    const words = keywords
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    if (!name.trim()) return setError("Give the rule a name");
    if (kind === "keyword" && words.length === 0) return setError("Add at least one keyword or phrase");
    setBusy(true);
    try {
      await api("/rules", {
        method: "POST",
        body: {
          name: name.trim(),
          kind,
          keywords: kind === "keyword" ? words : null,
          pattern: kind === "pattern" ? pattern : null,
          category: category.trim() || null,
          notify,
          enabled: true,
          chat_id: chat.trim() || null,
        },
      });
      setName("");
      setKeywords("");
      setCategory("");
      setChat("");
      onCreated();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={submit}>
      <h2>New rule</h2>
      <div className="form-grid">
        <div className="field">
          <label htmlFor={`${id}-name`}>Name</label>
          <input id={`${id}-name`} value={name} onChange={(e) => setName(e.target.value)} required />
        </div>
        <div className="field">
          <label htmlFor={`${id}-kind`}>Kind</label>
          <select id={`${id}-kind`} value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="keyword">Keywords</option>
            <option value="pattern">Pattern</option>
          </select>
        </div>
        {kind === "keyword" ? (
          <div className="field wide">
            <label htmlFor={`${id}-kw`}>Keywords and phrases, one per line</label>
            <textarea id={`${id}-kw`} rows={4} value={keywords} onChange={(e) => setKeywords(e.target.value)} />
          </div>
        ) : (
          <div className="field">
            <label htmlFor={`${id}-pat`}>Pattern</label>
            <select id={`${id}-pat`} value={pattern} onChange={(e) => setPattern(e.target.value)}>
              {PATTERNS.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="field">
          <label htmlFor={`${id}-cat`}>Category (optional)</label>
          <input id={`${id}-cat`} value={category} onChange={(e) => setCategory(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${id}-chat`}>Chat (optional, default all chats)</label>
          <input id={`${id}-chat`} list={`${id}-chats`} value={chat} onChange={(e) => setChat(e.target.value)} />
          <datalist id={`${id}-chats`}>
            {chats.filter((c) => c.chat_id).map((c) => (
              <option key={c.chat_id} value={c.chat_id} />
            ))}
          </datalist>
        </div>
      </div>
      <label className="check">
        <input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} />
        Create a notification when this rule matches
      </label>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <button type="submit" className="primary" disabled={busy}>
        {busy ? "Creating…" : "Create rule"}
      </button>
    </form>
  );
}

export default function Rules() {
  const [rules, setRules] = useState(null);
  const [chats, setChats] = useState([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api("/rules")
      .then((r) => {
        setRules(r);
        setError("");
      })
      .catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    api("/chats").then(setChats).catch(() => {});
  }, [load]);

  return (
    <>
      <h1>Rules</h1>
      <RuleForm chats={chats} onCreated={load} />
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!rules && !error && <p className="muted">Loading…</p>}
      {rules && rules.length === 0 && <p className="empty">No rules yet. Create one above to start tagging and flagging messages.</p>}
      <ul className="list">
        {(rules ?? []).map((r) => (
          <RuleRow
            key={r.id}
            rule={r}
            onChange={(next) => setRules((cur) => cur.map((x) => (x.id === next.id ? next : x)))}
            onDeleted={(rid) => setRules((cur) => cur.filter((x) => x.id !== rid))}
          />
        ))}
      </ul>
    </>
  );
}
