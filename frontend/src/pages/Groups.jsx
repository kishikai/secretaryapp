import { useCallback, useEffect, useId, useState } from "react";
import { api, isAbort, query } from "../api.js";
import { errorText, plural } from "../format.js";

function GroupForm({ group, onSaved, onCancel }) {
  const id = useId();
  const [name, setName] = useState(group?.name ?? "");
  const [description, setDescription] = useState(group?.description ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    if (!name.trim()) return setError("Give the group a name");
    setBusy(true);
    try {
      const body = { name: name.trim(), description: description.trim() || null };
      if (group) await api(`/groups/${group.id}`, { method: "PATCH", body });
      else await api("/groups", { method: "POST", body });
      if (!group) {
        setName("");
        setDescription("");
      }
      onSaved();
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
          <input id={`${id}-name`} value={name} onChange={(e) => setName(e.target.value)} required />
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
  const already = new Set((members ?? []).map((m) => m.id));

  const loadMembers = useCallback(
    () =>
      api(`/members${query({ group_id: group.id })}`)
        .then((m) => {
          setMembers(m);
          setError("");
        })
        .catch((e) => setError(errorText(e))),
    [group.id],
  );

  useEffect(() => {
    loadMembers();
  }, [loadMembers]);

  useEffect(() => {
    if (!canEdit) return undefined;
    const t = setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q, canEdit]);

  useEffect(() => {
    if (!canEdit) return undefined;
    const ctl = new AbortController();
    api(`/members${query({ q: debouncedQ, active: "true" })}`, {
      signal: ctl.signal,
    })
      .then((m) => {
        setResults(m);
        setSearchError("");
      })
      .catch((e) => !isAbort(e) && setSearchError(errorText(e)));
    return () => ctl.abort();
  }, [debouncedQ, canEdit]);

  const toggle = (mid) => setSelected((cur) => (cur.includes(mid) ? cur.filter((x) => x !== mid) : [...cur, mid]));
  const shown = (results ?? []).slice(0, MAX_SHOWN);

  const add = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const r = await api(`/groups/${group.id}/members`, {
        method: "POST",
        body: { member_ids: selected },
      });
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
      await loadMembers();
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="card" role="group" aria-label={`Members of ${group.name}`}>
      <h3>Members of {group.name}</h3>
      {members && members.length === 0 && <p className="empty">No one is in this group yet.</p>}
      {!members && !error && <p className="muted">Loading…</p>}
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
            <input id={`${id}-q`} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, email, phone or code" />
          </div>
          {searchError && (
            <p role="alert" className="error">
              {searchError}
            </p>
          )}
          {!results && !searchError && <p className="muted">Loading…</p>}
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
      <li>
        <GroupForm
          group={group}
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
    <li className="card item">
      <div className="item-head">
        <h2>{group.name}</h2>
        <a href={`#members?group=${group.id}`}>
          {plural(group.member_count, "member", "members")}
          <span className="sr-only"> in {group.name}</span>
        </a>
      </div>
      {group.description && <p className="text">{group.description}</p>}
      <div className="row-actions">
        {!managing && (
          <button type="button" className="small" onClick={() => setManaging(true)}>
            {canEdit ? "Manage members" : "View members"}
            <span className="sr-only"> of {group.name}</span>
          </button>
        )}
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
      {managing && <MembersPanel group={group} canEdit={canEdit} onChanged={onChanged} onClose={() => setManaging(false)} />}
      {confirming && (
        <p role="status" className="muted">
          Delete the group {group.name}? The members stay in the system; they just leave this group.
        </p>
      )}
      {err && (
        <p role="alert" className="error">
          {err}
        </p>
      )}
    </li>
  );
}

export default function Groups({ role }) {
  const [groups, setGroups] = useState(null);
  const [error, setError] = useState("");
  const canEdit = role === "secretary" || role === "assistant";
  const canDelete = role === "secretary";

  const load = useCallback(() => {
    api("/groups")
      .then((g) => {
        setGroups(g);
        setError("");
      })
      .catch((e) => setError(errorText(e)));
  }, []);

  useEffect(load, [load]);

  return (
    <>
      <h1>Groups</h1>
      {canEdit && <GroupForm onSaved={load} />}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!groups && !error && <p className="muted">Loading…</p>}
      {groups && groups.length === 0 && <p className="empty">No groups yet.</p>}
      <ul className="list">
        {(groups ?? []).map((g) => (
          <GroupRow key={g.id} group={g} canEdit={canEdit} canDelete={canDelete} onChanged={load} />
        ))}
      </ul>
    </>
  );
}
