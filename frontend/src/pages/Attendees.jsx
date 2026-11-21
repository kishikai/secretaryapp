import { useEffect, useId, useRef, useState } from "react";
import { api, isAbort, query } from "../api.js";
import { errorText, plural } from "../format.js";
import { toast } from "../toast.jsx";
import { attendanceLabel, useIdempotencyKey } from "./eventUi.jsx";

const MAX_SHOWN = 50;

function AddGroup({ event, onAdded, children }) {
  const id = useId();
  const [groups, setGroups] = useState([]);
  const [groupId, setGroupId] = useState("");
  const [busy, setBusy] = useState(false);
  const key = useIdempotencyKey();

  useEffect(() => {
    api("/groups")
      .then(setGroups)
      .catch(() => {});
  }, []);

  const add = async () => {
    setBusy(true);
    const ok = await onAdded(
      () =>
        api(`/events/${event.id}/attendees`, {
          method: "POST",
          body: { group_ids: [Number(groupId)] },
          idempotencyKey: key.get(`${event.id}:${groupId}`),
        }),
      "group",
    );
    if (ok) key.renew();
    setBusy(false);
  };

  return (
    <div className="inline-add">
      <div className="field">
        <label htmlFor={`${id}-group`} className="sr-only">
          Add a group
        </label>
        <select id={`${id}-group`} value={groupId} onChange={(e) => setGroupId(e.target.value)}>
          <option value="">Add a group…</option>
          {groups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name} ({g.member_count})
            </option>
          ))}
        </select>
      </div>
      <button type="button" disabled={!groupId || busy} onClick={add}>
        {busy ? "Adding…" : "Add group"}
      </button>
      {children}
    </div>
  );
}

function AddPeople({ event, onAdded, onClose }) {
  const id = useId();
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [results, setResults] = useState(null);
  const [selected, setSelected] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const key = useIdempotencyKey();
  const already = new Set((event.attendees ?? []).map((a) => a.member_id));

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    const ctl = new AbortController();
    api(`/members${query({ q: debouncedQ, active: "true" })}`, { signal: ctl.signal })
      .then((m) => {
        setResults(m);
        setError("");
      })
      .catch((e) => !isAbort(e) && setError(errorText(e)));
    return () => ctl.abort();
  }, [debouncedQ]);

  const toggle = (mid) => setSelected((cur) => (cur.includes(mid) ? cur.filter((x) => x !== mid) : [...cur, mid]));
  const shown = (results ?? []).slice(0, MAX_SHOWN);

  const add = async () => {
    setBusy(true);
    const ok = await onAdded(
      () =>
        api(`/events/${event.id}/attendees`, {
          method: "POST",
          body: { member_ids: selected },
          idempotencyKey: key.get(`${event.id}:${[...selected].sort((x, y) => x - y).join(",")}`),
        }),
      "people",
    );
    setBusy(false);
    if (ok) {
      key.renew();
      onClose();
    }
  };

  return (
    <div className="card" role="group" aria-label="Add people">
      <div className="field">
        <label htmlFor={`${id}-q`}>Search members</label>
        <input id={`${id}-q`} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, email, phone or code" />
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!results && !error && <p className="muted">Loading…</p>}
      {results && results.length === 0 && <p className="empty">No active members match.</p>}
      {shown.length > 0 && (
        <ul className="pick-list">
          {shown.map((m) => (
            <li key={m.id}>
              <label className="check">
                <input type="checkbox" checked={already.has(m.id) || selected.includes(m.id)} disabled={already.has(m.id)} onChange={() => toggle(m.id)} />
                <span>
                  {m.full_name}
                  {(m.email || m.phone) && <span className="muted"> · {m.email || m.phone}</span>}
                  {already.has(m.id) && <span className="muted"> (already on the list)</span>}
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {results && results.length > MAX_SHOWN && <p className="muted">Showing the first {MAX_SHOWN} of {results.length}. Type in the search box to narrow it down.</p>}
      <div className="row-actions">
        <button type="button" className="primary" disabled={selected.length === 0 || busy} onClick={add}>
          {busy ? "Adding…" : selected.length ? `Add ${plural(selected.length, "person", "people")}` : "Add selected"}
        </button>
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}

export default function Attendees({ event, canEdit, inSeries, defaultOpen = false, onChanged }) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const [picking, setPicking] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [removing, setRemoving] = useState(null);
  const listRef = useRef(null);
  const list = event.attendees ?? [];

  // Runs an add request; returns true on success. Shows the result or the API's reason.
  const added = async (request, kind) => {
    setError("");
    setNotice("");
    try {
      const r = await request();
      const n = r?.added ?? 0;
      setNotice(
        n === 0
          ? "No one new was added. They may already be on the list."
          : `Added ${plural(n, "person", "people")}.${kind === "group" ? " Later changes to the group won't change this event's list." : ""}`,
      );
      await onChanged();
      return true;
    } catch (e) {
      setError(errorText(e));
      return false;
    }
  };

  // Removing takes effect at once and can be undone from the toast. Removing deletes the person's mark for this
  // event along with them, so Undo adds them back and records the same status again.
  const remove = async (a) => {
    const mark = a.status && a.status !== "not_recorded" ? a.status : null;
    const index = list.findIndex((x) => x.member_id === a.member_id);
    setRemoving(a.member_id);
    setError("");
    setNotice("");
    try {
      await api(`/events/${event.id}/attendees/${a.member_id}`, { method: "DELETE" });
      await onChanged();
      toast({
        text: mark
          ? `Removed ${a.full_name} from the list. Undo puts them back as ${attendanceLabel(mark)}, but not who recorded it or when.`
          : `Removed ${a.full_name} from the list.`,
        action: {
          label: "Undo",
          run: async () => {
            await api(`/events/${event.id}/attendees`, { method: "POST", body: { member_ids: [a.member_id] } });
            let lost = null;
            if (mark && !event.finalized_at) {
              try {
                await api(`/events/${event.id}/attendance`, {
                  method: "PUT",
                  body: { records: [{ member_id: a.member_id, status: mark }], reason: "Restored after being removed from the list" },
                });
              } catch (e) {
                lost = e;
              }
            }
            await onChanged();
            if (lost) throw new Error(`${a.full_name} is back on the list, but their ${attendanceLabel(mark)} mark was not restored: ${errorText(lost)}`);
          },
        },
      });
      // The button that was pressed is gone: keep keyboard focus in the list.
      requestAnimationFrame(() => {
        const buttons = listRef.current?.querySelectorAll(".att-row button");
        const next = buttons?.[Math.min(index, (buttons?.length ?? 1) - 1)];
        (next ?? listRef.current?.closest("details")?.querySelector("summary"))?.focus();
      });
    } catch (e) {
      setError(errorText(e));
    } finally {
      setRemoving(null);
    }
  };

  return (
    <details className="card attendees-panel" open={isOpen} onToggle={(e) => setIsOpen(e.currentTarget.open)}>
      <summary>
        <span>Who&rsquo;s expected ({list.length})</span>
        {canEdit && <span className="muted att-hint">Manage list</span>}
      </summary>
      {canEdit && inSeries && <p className="muted">Each day has its own list. Changes here only affect this day.</p>}
      {canEdit && (
        <>
          <AddGroup event={event} onAdded={added}>
            {!picking && (
              <button type="button" onClick={() => setPicking(true)}>
                Add people
              </button>
            )}
          </AddGroup>
          {picking && <AddPeople event={event} onAdded={added} onClose={() => setPicking(false)} />}
        </>
      )}
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {list.length === 0 && <p className="empty">No one is on the expected list yet.</p>}
      <ul className="att-list" ref={listRef}>
        {list.map((a) => (
          <li key={a.member_id} className="att-row">
            <div className="att-who">
              <strong>{a.full_name}</strong>
              {!a.active && <span className="chip">Inactive</span>}
              {(a.email || a.phone) && <span className="muted att-contact">{[a.email, a.phone].filter(Boolean).join(" · ")}</span>}
            </div>
            {canEdit && (
              <button type="button" className="small" disabled={removing === a.member_id} onClick={() => remove(a)}>
                Remove<span className="sr-only"> {a.full_name}</span>
              </button>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}
