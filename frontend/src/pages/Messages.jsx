import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api, ApiError, isAbort, query } from "../api.js";
import { chatLabel, formatConfidence, formatTime, plural, sourceLabel } from "../format.js";

const PAGE = 30;
const NO_CHAT = "__none__";

// Backend has no "chat is null" filter in the contract, so that choice is applied client-side.
function buildParams(f, beforeId) {
  const day = (d, t) => (d ? `${d}T${t}Z` : "");
  return {
    q: f.q,
    chat_id: f.chat === NO_CHAT ? "" : f.chat,
    source: f.source,
    category: f.category,
    since: day(f.from, "00:00:00.000"),
    until: day(f.to, "23:59:59.999"),
    before_id: beforeId,
    limit: PAGE,
  };
}

function Chip({ children, accent }) {
  return <span className={`chip${accent ? " accent" : ""}`}>{children}</span>;
}

function MessageMeta({ m }) {
  const conf = formatConfidence(m.confidence);
  return (
    <>
      <span>{chatLabel(m.chat_id)}</span>
      <span>{formatTime(m.created_at)}</span>
      {(m.sources ?? [m.source]).map((s) => (
        <Chip key={s}>{sourceLabel(s)}</Chip>
      ))}
      {(m.categories ?? []).map((c) => (
        <Chip key={c} accent>
          {c}
        </Chip>
      ))}
      {conf && m.source === "ocr" && <span>OCR confidence {conf}</span>}
    </>
  );
}

function MessageDetail({ message, onClose }) {
  const [variants, setVariants] = useState(null);
  const [error, setError] = useState("");
  const headRef = useRef(null);

  useEffect(() => {
    headRef.current?.focus();
  }, [message.id]);

  useEffect(() => {
    const ctl = new AbortController();
    setVariants(null);
    setError("");
    api(`/messages/${message.id}/variants`, { signal: ctl.signal })
      .then(setVariants)
      .catch((e) => !isAbort(e) && setError(e.message));
    return () => ctl.abort();
  }, [message.id]);

  return (
    <aside className="card detail" aria-labelledby="detail-title">
      <div className="item-head">
        <h2 id="detail-title" tabIndex={-1} ref={headRef}>
          Message #{message.id}
        </h2>
        <button type="button" className="small" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="text">{message.message_text}</p>
      <div className="meta">
        {message.sender && <span>From {message.sender}</span>}
        <MessageMeta m={message} />
      </div>
      <h3>Variants</h3>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!variants && !error && <p className="muted">Loading…</p>}
      {variants && variants.length === 0 && <p className="muted">No variants recorded.</p>}
      <ul className="list">
        {(variants ?? []).map((v) => (
          <li key={v.id} className="variant">
            <div className="meta">
              <Chip>{sourceLabel(v.source)}</Chip>
              {formatConfidence(v.confidence) && <span>Confidence {formatConfidence(v.confidence)}</span>}
              <span>{formatTime(v.created_at)}</span>
            </div>
            <p className="text">{v.message_text}</p>
          </li>
        ))}
      </ul>
    </aside>
  );
}

function UploadForm({ chats, onUploaded }) {
  const id = useId();
  const [file, setFile] = useState(null);
  const [chat, setChat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const fileRef = useRef(null);

  const submit = async (e) => {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setResult(null);
    const form = new FormData();
    form.append("file", file);
    if (chat.trim()) form.append("chat_id", chat.trim());
    try {
      const r = await api("/ocr/screenshots", { method: "POST", form });
      const msgs = r.messages ?? [];
      setResult({
        added: msgs.filter((m) => !m.duplicate).length,
        duplicates: msgs.filter((m) => m.duplicate).length,
        skipped: (r.skipped_lines ?? []).length,
        rejected: r.rejected ?? [],
      });
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      onUploaded();
    } catch (err) {
      if (err instanceof ApiError && err.status === 413) setError("That image is too large. Try a smaller or cropped screenshot.");
      else if (err instanceof ApiError && err.status === 422) setError("That file is not an image the server can read. Upload a PNG or JPEG screenshot.");
      else if (err instanceof ApiError && err.status === 503) setError("The OCR engine is not installed on the server, so screenshots cannot be read yet.");
      else setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <details className="card">
      <summary>Upload screenshot</summary>
      <form onSubmit={submit} className="upload">
        <div className="field">
          <label htmlFor={`${id}-f`}>Screenshot image</label>
          <input id={`${id}-f`} ref={fileRef} type="file" accept="image/*" onChange={(e) => setFile(e.target.files[0] ?? null)} required />
        </div>
        <div className="field">
          <label htmlFor={`${id}-c`}>Chat (optional)</label>
          <input id={`${id}-c`} list={`${id}-chats`} value={chat} onChange={(e) => setChat(e.target.value)} />
          <datalist id={`${id}-chats`}>
            {chats.filter((c) => c.chat_id).map((c) => (
              <option key={c.chat_id} value={c.chat_id} />
            ))}
          </datalist>
        </div>
        <button type="submit" className="primary" disabled={busy || !file}>
          {busy ? "Reading screenshot…" : "Upload"}
        </button>
      </form>
      <div role="status">
        {result && (
          <p>
            {result.added} new, {plural(result.duplicates, "duplicate", "duplicates")}, {plural(result.skipped, "skipped line", "skipped lines")}
            {result.rejected.length > 0 && `, ${result.rejected.length} rejected`}.
          </p>
        )}
      </div>
      {result?.rejected.length > 0 && (
        <ul className="muted">
          {result.rejected.map((r, i) => (
            <li key={i}>{typeof r === "string" ? r : JSON.stringify(r)}</li>
          ))}
        </ul>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </details>
  );
}

export default function Messages() {
  const id = useId();
  const [chats, setChats] = useState([]);
  const [categories, setCategories] = useState([]);
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [chat, setChat] = useState("");
  const [source, setSource] = useState("");
  const [category, setCategory] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const [items, setItems] = useState([]);
  const [lastId, setLastId] = useState(null); // last raw id, for paging
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState(null);
  const triggerRef = useRef(null);
  const gen = useRef(0);

  useEffect(() => {
    const t = setTimeout(() => setQ(qInput.trim()), 300);
    return () => clearTimeout(t);
  }, [qInput]);

  const loadFilters = useCallback(() => {
    api("/chats").then(setChats).catch(() => {});
    api("/stats").then((s) => setCategories(s.by_category ?? [])).catch(() => {});
  }, []);
  useEffect(loadFilters, [loadFilters]);

  const filters = { q, chat, source, category, from, to };
  const fetchPage = (beforeId, signal) =>
    api(`/messages${query(buildParams(filters, beforeId))}`, { signal }).then((rows) => ({
      rows,
      shown: chat === NO_CHAT ? rows.filter((m) => m.chat_id === null) : rows,
    }));

  const errorFor = (e) =>
    e instanceof ApiError && e.status === 422 && q ? "Search needs at least one letter or digit" : e.message;

  useEffect(() => {
    const my = ++gen.current;
    const ctl = new AbortController();
    setLoading(true);
    setError("");
    fetchPage(null, ctl.signal)
      .then(({ rows, shown }) => {
        if (my !== gen.current) return;
        setItems(shown);
        setLastId(rows.length ? rows[rows.length - 1].id : null);
        setHasMore(rows.length === PAGE);
      })
      .catch((e) => {
        if (isAbort(e) || my !== gen.current) return;
        setItems([]);
        setHasMore(false);
        setError(errorFor(e));
      })
      .finally(() => my === gen.current && setLoading(false));
    return () => ctl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, chat, source, category, from, to, reloadKey]);

  const loadOlder = async () => {
    const my = gen.current;
    setLoadingMore(true);
    try {
      const { rows, shown } = await fetchPage(lastId);
      if (my !== gen.current) return;
      setItems((cur) => [...cur, ...shown]);
      setLastId(rows.length ? rows[rows.length - 1].id : lastId);
      setHasMore(rows.length === PAGE);
    } catch (e) {
      setError(errorFor(e));
    } finally {
      setLoadingMore(false);
    }
  };

  const open = (m, el) => {
    triggerRef.current = el;
    setSelected(m);
  };
  const close = () => {
    setSelected(null);
    const el = triggerRef.current;
    if (el && el.isConnected) el.focus();
  };

  const filtered = Boolean(q || chat || source || category || from || to);
  const clear = () => {
    setQInput("");
    setQ("");
    setChat("");
    setSource("");
    setCategory("");
    setFrom("");
    setTo("");
  };

  return (
    <>
      <h1>Messages</h1>
      <UploadForm
        chats={chats}
        onUploaded={() => {
          setReloadKey((k) => k + 1);
          loadFilters();
        }}
      />

      <form className="card filters" role="search" onSubmit={(e) => e.preventDefault()}>
        <div className="field wide">
          <label htmlFor={`${id}-q`}>Search messages</label>
          <input id={`${id}-q`} type="search" value={qInput} onChange={(e) => setQInput(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${id}-chat`}>Chat</label>
          <select id={`${id}-chat`} value={chat} onChange={(e) => setChat(e.target.value)}>
            <option value="">All chats</option>
            {chats.map((c) => (
              <option key={c.chat_id ?? NO_CHAT} value={c.chat_id ?? NO_CHAT}>
                {chatLabel(c.chat_id)} ({c.count})
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${id}-src`}>Source</label>
          <select id={`${id}-src`} value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">All sources</option>
            <option value="clipboard">Clipboard</option>
            <option value="ocr">OCR</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${id}-cat`}>Category</label>
          <select id={`${id}-cat`} value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {categories.map((c) => (
              <option key={c.category} value={c.category}>
                {c.category} ({c.count})
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${id}-from`}>From date (UTC)</label>
          <input id={`${id}-from`} type="date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${id}-to`}>To date (UTC)</label>
          <input id={`${id}-to`} type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} />
        </div>
        {filtered && (
          <div className="field">
            <button type="button" onClick={clear}>
              Clear filters
            </button>
          </div>
        )}
      </form>

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <p role="status" className="sr-only">
        {loading ? "Loading messages" : error ? "" : `${plural(items.length, "message", "messages")} shown`}
      </p>
      {loading && <p className="muted">Loading…</p>}
      {!loading && !error && items.length === 0 && (
        <p className="empty">
          {filtered
            ? "No messages match these filters. Try a different search or clear the filters."
            : "No messages yet. Copy messages from a Messenger group chat or upload a screenshot to get started."}
        </p>
      )}

      <div className={`split${selected ? " has-detail" : ""}`}>
        <div>
          <ul className="list">
            {items.map((m) => (
              <li key={m.id} className="card item">
                <button
                  type="button"
                  className="msg-open"
                  aria-current={selected?.id === m.id ? "true" : undefined}
                  onClick={(e) => open(m, e.currentTarget)}
                >
                  <span className="text clamp">{m.message_text}</span>
                  <span className="meta">
                    <MessageMeta m={m} />
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {hasMore && !loading && (
            <button type="button" onClick={loadOlder} disabled={loadingMore}>
              {loadingMore ? "Loading…" : "Load older"}
            </button>
          )}
        </div>
        {selected && <MessageDetail message={selected} onClose={close} />}
      </div>
    </>
  );
}
