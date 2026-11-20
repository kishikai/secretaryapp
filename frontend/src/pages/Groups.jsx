import { useCallback, useEffect, useId, useState } from "react";
import { api } from "../api.js";
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

function GroupRow({ group, canEdit, canDelete, onChanged }) {
  const [editing, setEditing] = useState(false);
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
      {(canEdit || canDelete) && (
        <div className="row-actions">
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
      )}
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
