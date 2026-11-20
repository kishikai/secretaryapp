import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api, isAbort, query } from "../api.js";
import { errorText, plural } from "../format.js";
import MemberHistory from "./MemberHistory.jsx";
import MemberImport from "./MemberImport.jsx";

const EMPTY = { full_name: "", email: "", phone: "", member_code: "", notes: "", joined_on: "", left_on: "" };

function MemberForm({ member, groups, onSaved, onCancel }) {
  const id = useId();
  const headRef = useRef(null);
  const createdRef = useRef(null);
  const [f, setF] = useState(() => {
    if (!member) return EMPTY;
    return Object.fromEntries(Object.keys(EMPTY).map((k) => [k, member[k] ?? ""]));
  });
  const [groupIds, setGroupIds] = useState(member?.group_ids ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    headRef.current?.focus();
  }, []);

  const set = (k) => (e) => setF((cur) => ({ ...cur, [k]: e.target.value }));
  const toggleGroup = (gid) =>
    setGroupIds((cur) => (cur.includes(gid) ? cur.filter((x) => x !== gid) : [...cur, gid]));

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    if (!f.full_name.trim()) return setError("Enter the member's full name");
    const body = {
      full_name: f.full_name.trim(),
      email: f.email.trim() || null,
      phone: f.phone.trim() || null,
      member_code: f.member_code.trim() || null,
      notes: f.notes.trim() || null,
      joined_on: f.joined_on || null,
      left_on: f.left_on || null,
    };
    setBusy(true);
    try {
      // After a first save whose group step failed, a retry updates that member instead of adding another.
      const target = member ?? createdRef.current;
      if (!target) createdRef.current = await api("/members", { method: "POST", body });
      else await api(`/members/${target.id}`, { method: "PATCH", body });
      await api(`/members/${(member ?? createdRef.current).id}/groups`, { method: "PUT", body: { group_ids: groupIds } });
      onSaved();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const field = (k, label, props = {}) => (
    <div className="field">
      <label htmlFor={`${id}-${k}`}>{label}</label>
      <input id={`${id}-${k}`} value={f[k]} onChange={set(k)} {...props} />
    </div>
  );

  return (
    <form className="card" onSubmit={submit}>
      <h2 tabIndex={-1} ref={headRef}>
        {member ? `Edit ${member.full_name}` : "Add a member"}
      </h2>
      <div className="form-grid">
        {field("full_name", "Full name (required)", { required: true })}
        {field("email", "Email", { type: "email" })}
        {field("phone", "Phone", { type: "tel" })}
        {field("member_code", "Member code")}
        {field("joined_on", "Joined on", { type: "date" })}
        {field("left_on", "Left on", { type: "date" })}
        <div className="field wide">
          <label htmlFor={`${id}-notes`}>Notes</label>
          <textarea id={`${id}-notes`} rows={3} value={f.notes} onChange={set("notes")} />
        </div>
      </div>
      <fieldset className="groups-pick">
        <legend>Groups</legend>
        {groups.length === 0 && <p className="muted">No groups yet. Add some on the Groups tab.</p>}
        {groups.map((g) => (
          <label key={g.id} className="check">
            <input type="checkbox" checked={groupIds.includes(g.id)} onChange={() => toggleGroup(g.id)} />
            {g.name}
          </label>
        ))}
      </fieldset>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="row-actions">
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Saving…" : member ? "Save changes" : "Add member"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function MemberRow({ member, groupNames, canEdit, canDeactivate, onEdit, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const setActive = async (active) => {
    setBusy(true);
    setErr("");
    try {
      await api(`/members/${member.id}`, { method: "PATCH", body: { active } });
      onChanged();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const names = member.group_ids.map((gid) => groupNames.get(gid)).filter(Boolean);

  return (
    <li className={`card item${member.active ? "" : " is-read"}`}>
      <div className="item-head">
        <h2>{member.full_name}</h2>
        {!member.active && <span className="chip">Inactive</span>}
      </div>
      <div className="meta">
        {member.email && <span>{member.email}</span>}
        {member.phone && <span>{member.phone}</span>}
        {member.member_code && <span>Code {member.member_code}</span>}
      </div>
      {names.length > 0 && (
        <div className="meta">
          <span className="sr-only">Groups:</span>
          {names.map((n) => (
            <span key={n} className="chip accent">
              {n}
            </span>
          ))}
        </div>
      )}
      <div className="row-actions">
        <a className="link-button" href={`#members?id=${member.id}`}>
          History<span className="sr-only"> of {member.full_name}</span>
        </a>
        {canEdit && (
          <button type="button" className="small" onClick={() => onEdit(member)}>
            Edit<span className="sr-only"> {member.full_name}</span>
          </button>
        )}
        {canDeactivate && (
          <button type="button" className="small" onClick={() => setActive(!member.active)} disabled={busy}>
            {member.active ? "Deactivate" : "Reactivate"}
            <span className="sr-only"> {member.full_name}</span>
          </button>
        )}
      </div>
      {err && (
        <p role="alert" className="error">
          {err}
        </p>
      )}
    </li>
  );
}

function MemberList({ role, groupParam }) {
  const id = useId();
  const canEdit = role === "secretary" || role === "assistant";
  const canImport = role === "secretary";
  const [view, setView] = useState("list"); // "list" | "import"
  const [editing, setEditing] = useState(null); // null | "new" | member
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [group, setGroup] = useState(groupParam ?? "");
  const [active, setActive] = useState("");
  const [groups, setGroups] = useState([]);
  const [members, setMembers] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [version, setVersion] = useState(0);

  useEffect(() => {
    setGroup(groupParam ?? "");
  }, [groupParam]);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  const loadGroups = useCallback(() => {
    api("/groups")
      .then(setGroups)
      .catch(() => {});
  }, []);
  useEffect(loadGroups, [loadGroups]);

  useEffect(() => {
    if (view !== "list") return undefined;
    const ctl = new AbortController();
    api(`/members${query({ q: debouncedQ, group_id: group, active })}`, { signal: ctl.signal })
      .then((m) => {
        setMembers(m);
        setError("");
      })
      .catch((e) => !isAbort(e) && setError(errorText(e)));
    return () => ctl.abort();
  }, [view, debouncedQ, group, active, version]);

  const refresh = () => {
    setVersion((v) => v + 1);
    loadGroups(); // member counts may have changed
  };
  const groupNames = new Map(groups.map((g) => [g.id, g.name]));

  if (view === "import") {
    return (
      <MemberImport
        onCancel={() => setView("list")}
        onDone={(r) => {
          setNotice(
            `Import finished: ${plural(r.created, "member", "members")} added, ${r.updated} updated, ${r.skipped} skipped.`,
          );
          setView("list");
          refresh();
        }}
      />
    );
  }

  return (
    <>
      <h1>Members</h1>
      <div className="toolbar">
        {canEdit && !editing && (
          <button
            type="button"
            className="primary"
            onClick={() => {
              setNotice("");
              setEditing("new");
            }}
          >
            Add member
          </button>
        )}
        {canImport && !editing && (
          <button type="button" onClick={() => setView("import")}>
            Import from spreadsheet
          </button>
        )}
      </div>
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      {editing && (
        <MemberForm
          key={editing === "new" ? "new" : editing.id}
          member={editing === "new" ? null : editing}
          groups={groups}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            refresh();
          }}
        />
      )}
      <div className="card filters">
        <div className="field">
          <label htmlFor={`${id}-q`}>Search</label>
          <input id={`${id}-q`} type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, email, phone or code" />
        </div>
        <div className="field">
          <label htmlFor={`${id}-group`}>Group</label>
          <select id={`${id}-group`} value={group} onChange={(e) => setGroup(e.target.value)}>
            <option value="">All groups</option>
            {groups.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor={`${id}-active`}>Show</label>
          <select id={`${id}-active`} value={active} onChange={(e) => setActive(e.target.value)}>
            <option value="">All members</option>
            <option value="true">Active only</option>
            <option value="false">Inactive only</option>
          </select>
        </div>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!members && !error && <p className="muted">Loading…</p>}
      {members && (
        <p className="muted" role="status">
          {plural(members.length, "member", "members")}
        </p>
      )}
      {members && members.length === 0 && <p className="empty">No members match. Change the search or filters, or add a member.</p>}
      <ul className="list">
        {(members ?? []).map((m) => (
          <MemberRow
            key={m.id}
            member={m}
            groupNames={groupNames}
            canEdit={canEdit}
            canDeactivate={role === "secretary"}
            onEdit={(mem) => {
              setNotice("");
              setEditing(mem);
              window.scrollTo({ top: 0 });
            }}
            onChanged={refresh}
          />
        ))}
      </ul>
    </>
  );
}

export default function Members({ role, groupParam, idParam }) {
  if (idParam) return <MemberHistory key={idParam} id={idParam} />;
  return <MemberList role={role} groupParam={groupParam} />;
}
