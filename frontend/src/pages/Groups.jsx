import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api, isAbort, query, swr } from "../api.js";
import { errorText, plural } from "../format.js";
import { toast } from "../toast.jsx";
import { SkeletonRows } from "../ui.jsx";
import { useIdempotency } from "./idempotency.js";

function GroupForm({ group, onSaved, onCancel, autoFocus }) {
  const id = useId();
  const [name, setName] = useState(group?.name ?? "");
  const [description, setDescription] = useState(group?.description ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const idem = useIdempotency();

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    if (!name.trim()) return setError("Give the group a name");
    setBusy(true);
    try {
      const body = { name: name.trim(), description: description.trim() || null };
      if (group) await api(`/groups/${group.id}`, { method: "PATCH", body });
      else {
        await api("/groups", { method: "POST", body, idempotencyKey: idem.keyFor(body) });
        idem.done();
      }
      if (!group) {
        setName("");
        setDescription("");
      }
      onSaved(body.name);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={submit}>
      <h2>{group ? `Edit ${group.name}` : "New group"}</h2>
      <div className="form-grid">
        <div className="field">
          <label htmlFor={`${id}-name`}>Name</label>
          <input id={`${id}-name`} value={name} onChange={(e) => setName(e.target.value)} required autoFocus={autoFocus} autoCapitalize="words" />
        </div>
        <div className="field">
          <label htmlFor={`${id}-desc`}>Description (optional)</label>
          <input id={`${id}-desc`} value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="row-actions">
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Saving…" : group ? "Save changes" : "Add group"}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}

const MAX_SHOWN = 50;

function MembersPanel({ group, canEdit, onChanged, onClose }) {
  const id = useId();
  const [members, setMembers] = useState(null);
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [results, setResults] = useState(null);
  const [selected, setSelected] = useState([]);
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [searchError, setSearchError] = useState("");
  const headRef = useRef(null);
  const loadCtl = useRef(null);
  const idem = useIdempotency();
  const already = new Set((members ?? []).map((m) => m.id));

  // The newest load wins: starting one drops the answer of an older one.
  const loadMembers = useCallback(() => {
    loadCtl.current?.abort();
    const ctl = new AbortController();
    loadCtl.current = ctl;
    return swr(
      `/members${query({ group_id: group.id })}`,
      (m, { stale }) => {
        setMembers(m);
        if (!stale) setError("");
      },
      { signal: ctl.signal },
    ).catch((e) => !isAbort(e) && setError(errorText(e)));
  }, [group.id]);

  useEffect(() => {
    loadMembers();
    return () => loadCtl.current?.abort();
  }, [loadMembers]);

  useEffect(() => {
    if (!canEdit) return undefined;
    const t = setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q, canEdit]);

  useEffect(() => {
    if (!canEdit) return undefined;
    const ctl = new AbortController();
    swr(
      `/members${query({ q: debouncedQ, active: "true" })}`,
      (m, { stale }) => {
        setResults(m);
        if (!stale) setSearchError("");
      },
      { signal: ctl.signal },
    ).catch((e) => !isAbort(e) && setSearchError(errorText(e)));
    return () => ctl.abort();
  }, [debouncedQ, canEdit]);

  const toggle = (mid) => setSelected((cur) => (cur.includes(mid) ? cur.filter((x) => x !== mid) : [...cur, mid]));
  const shown = (results ?? []).slice(0, MAX_SHOWN);

  const add = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const body = { member_ids: selected };
      const r = await api(`/groups/${group.id}/members`, { method: "POST", body, idempotencyKey: idem.keyFor(body) });
      idem.done();
      setNotice(`Added ${r?.added ?? selected.length}.`);
      setSelected([]);
      await loadMembers();
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (m) => {
    setRemoving(m.id);
    setError("");
    setNotice("");
    try {
      await api(`/groups/${group.id}/members/${m.id}`, { method: "DELETE" });
      toast({
        text: `Removed ${m.full_name} from ${group.name}.`,
        action: {
          label: "Undo",
          run: async () => {
            await api(`/groups/${group.id}/members`, { method: "POST", body: { member_ids: [m.id] } });
            await loadMembers();
            onChanged();
          },
        },
      });
      await loadMembers();
      onChanged();
      headRef.current?.focus(); // the Remove button is gone; keep the place
    } catch (e) {
      setError(errorText(e));
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="card members-panel" role="group" aria-label={`Members of ${group.name}`}>
      <h3 tabIndex={-1} ref={headRef}>
        Members of {group.name}
      </h3>
      {members && members.length === 0 && <p className="empty">No one is in this group yet.</p>}
      {!members && !error && <SkeletonRows rows={3} card={false} label="Loading members" />}
      <ul className="attendees">
        {(members ?? []).map((m) => (
          <li key={m.id} className="attendee">
            <div className="who">
              <strong>{m.full_name}</strong>
              {!m.active && (
                <>
                  {" "}
                  <span className="chip">Inactive</span>
                </>
              )}
            </div>
            {canEdit && (
              <button type="button" className="small" disabled={removing === m.id} onClick={() => remove(m)}>
                Remove
                <span className="sr-only">
                  {" "}
                  {m.full_name} from {group.name}
                </span>
              </button>
            )}
          </li>
        ))}
      </ul>
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
      {canEdit && (
        <>
          <h3>Add members</h3>
          <p className="muted">
            Pick people who are already members. Someone not in the list yet? <a href="#members">Add a new person on the Members page</a>.
          </p>
          <div className="field">
            <label htmlFor={`${id}-q`}>Search members</label>
            <input id={`${id}-q`} type="search" enterKeyHint="search" autoComplete="off" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, email, phone or code" />
          </div>
          {searchError && (
            <p role="alert" className="error">
              {searchError}
            </p>
          )}
          {!results && !searchError && <SkeletonRows rows={3} card={false} label="Loading people to add" />}
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
                      {already.has(m.id) && <span className="muted"> (Already in this group)</span>}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          {results && results.length > MAX_SHOWN && (
            <p className="muted">
              Showing the first {MAX_SHOWN} of {results.length}. Type in the search box to narrow it down.
            </p>
          )}
        </>
      )}
      <div className="row-actions">
        {canEdit && (
          <button type="button" className="primary" disabled={selected.length === 0 || busy} onClick={add}>
            {busy ? "Adding…" : selected.length ? `Add ${selected.length} to ${group.name}` : "Add selected"}
          </button>
        )}
        <button type="button" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
}

function GroupRow({ group, canEdit, canDelete, onChanged }) {
  const [editing, setEditing] = useState(false);
  const [managing, setManaging] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const toggleRef = useRef(null);

  const remove = async () => {
    setBusy(true);
    setErr("");
    try {
      await api(`/groups/${group.id}`, { method: "DELETE" });
      onChanged();
    } catch (e) {
      setErr(errorText(e));
      setConfirming(false);
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
    return (
      <li className="row row-form">
        <GroupForm
          group={group}
          autoFocus
          onCancel={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            onChanged();
          }}
        />
      </li>
    );
  }

  return (
    <li className="row">
      <div className="row-main">
        <div className="row-title">{group.name}</div>
        {group.description && (
          <div className="row-sub">
            <span>{group.description}</span>
          </div>
        )}
      </div>
      <div className="row-side">
        <a className="group-count" href={`#members?group=${group.id}`}>
          {plural(group.member_count, "member", "members")}
          <span className="sr-only"> in {group.name}</span>
        </a>
      </div>
      <div className="row-actions-inline">
        <button type="button" className="small" ref={toggleRef} aria-expanded={managing} onClick={() => setManaging((m) => !m)}>
          {canEdit ? "Members" : "View"}
          <span className="sr-only"> of {group.name}</span>
        </button>
        {canEdit && (
          <button type="button" className="small" onClick={() => setEditing(true)}>
            Edit<span className="sr-only"> {group.name}</span>
          </button>
        )}
        {canDelete &&
          (confirming ? (
            <span className="confirm">
              <button type="button" className="small danger" onClick={remove} disabled={busy}>
                Yes, delete<span className="sr-only"> {group.name}</span>
              </button>
              <button type="button" className="small" onClick={() => setConfirming(false)}>
                Cancel
              </button>
            </span>
          ) : (
            <button type="button" className="small" onClick={() => setConfirming(true)} disabled={busy}>
              Delete<span className="sr-only"> {group.name}</span>
            </button>
          ))}
      </div>
      {managing && (
        <MembersPanel
          group={group}
          canEdit={canEdit}
          onChanged={onChanged}
          onClose={() => {
            setManaging(false);
            setTimeout(() => toggleRef.current?.focus(), 0);
          }}
        />
      )}
      {confirming && (
        <p role="status" className="muted row-note">
          Delete the group {group.name}? The members stay in the system; they just leave this group.
        </p>
      )}
      {err && (
        <p role="alert" className="error row-note">
          {err}
        </p>
      )}
    </li>
  );
}

export default function Groups({ role }) {
  const [groups, setGroups] = useState(null);
  const [error, setError] = useState("");
  const [formOpen, setFormOpen] = useState(null); // null = automatic (open while there are no groups)
  const [notice, setNotice] = useState("");
  const canEdit = role === "secretary" || role === "assistant";
  const canDelete = role === "secretary";
  const showForm = canEdit && (formOpen ?? (groups !== null && groups.length === 0));

  const loadCtl = useRef(null);
  // The newest load wins: starting one drops the answer of an older one.
  const load = useCallback(() => {
    loadCtl.current?.abort();
    const ctl = new AbortController();
    loadCtl.current = ctl;
    swr(
      "/groups",
      (g, { stale }) => {
        setGroups(g);
        if (!stale) setError("");
      },
      { signal: ctl.signal },
    ).catch((e) => !isAbort(e) && setError(errorText(e))); // a failed refresh keeps the cached list on screen
  }, []);

  useEffect(() => {
    load();
    return () => loadCtl.current?.abort();
  }, [load]);

  return (
    <>
      <div className="page-head">
        <h1>Groups</h1>
        {canEdit && (
          <div className="actions">
            <button
              type="button"
              className="primary"
              aria-expanded={showForm}
              aria-controls="new-group-form"
              onClick={() => {
                setNotice("");
                setFormOpen(!showForm);
              }}
            >
              New group
            </button>
          </div>
        )}
      </div>
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      {showForm && (
        <div id="new-group-form">
          <GroupForm
            autoFocus={formOpen === true}
            onCancel={() => setFormOpen(false)}
            onSaved={(name) => {
              setFormOpen(false);
              setNotice(`Added ${name}.`);
              load();
            }}
          />
        </div>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!groups && !error && <SkeletonRows rows={4} label="Loading groups" />}
      {groups && groups.length === 0 && <p className="empty">No groups yet.</p>}
      {groups && groups.length > 0 && (
        <ul className="rows">
          {groups.map((g) => (
            <GroupRow key={g.id} group={g} canEdit={canEdit} canDelete={canDelete} onChanged={load} />
          ))}
        </ul>
      )}
    </>
  );
}
