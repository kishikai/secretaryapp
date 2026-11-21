import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api, isAbort, query, swr } from "../api.js";
import { errorText, plural } from "../format.js";
import Sheet from "../Sheet.jsx";
import { toast } from "../toast.jsx";
import { SkeletonRows, useIsPhone } from "../ui.jsx";
import { useIdempotency } from "./idempotency.js";
import MemberHistory from "./MemberHistory.jsx";
import MemberImport from "./MemberImport.jsx";

const EMPTY = { full_name: "", email: "", phone: "", member_code: "", notes: "", joined_on: "", left_on: "" };

function MemberForm({ member, groups, canDeactivate, onSaved, onToggled, onCancel }) {
  const id = useId();
  const headRef = useRef(null);
  const createdRef = useRef(null);
  const idem = useIdempotency();
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
      if (!target) {
        createdRef.current = await api("/members", { method: "POST", body, idempotencyKey: idem.keyFor(body) });
        idem.done();
      }
      else await api(`/members/${target.id}`, { method: "PATCH", body });
      await api(`/members/${(member ?? createdRef.current).id}/groups`, { method: "PUT", body: { group_ids: groupIds } });
      onSaved();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async () => {
    setError("");
    setBusy(true);
    try {
      await api(`/members/${member.id}`, { method: "PATCH", body: { active: !member.active } });
      onToggled(member, !member.active);
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
    <form className="card member-form" onSubmit={submit}>
      <h2 tabIndex={-1} ref={headRef}>
        {member ? `Edit ${member.full_name}` : "Add a member"}
      </h2>
      <div className="form-grid">
        {field("full_name", "Full name (required)", { required: true, autoComplete: "name", autoCapitalize: "words" })}
        {field("email", "Email", { type: "email", autoComplete: "email", inputMode: "email" })}
        {field("phone", "Phone", { type: "tel", autoComplete: "tel", inputMode: "tel" })}
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
      <div className="row-actions form-actions">
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Saving…" : member ? "Save changes" : "Add member"}
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
        {member && canDeactivate && (
          <button type="button" className="form-actions-end" onClick={toggleActive} disabled={busy}>
            {member.active ? "Deactivate member" : "Reactivate member"}
          </button>
        )}
      </div>
    </form>
  );
}

function MemberRow({ member, groupNames, canEdit, uid, onEdit }) {
  const names = member.group_ids.map((gid) => groupNames.get(gid)).filter(Boolean);
  const sub = [member.email, member.phone, member.member_code && `Code ${member.member_code}`].filter(Boolean);

  return (
    <li className={`row${member.active ? "" : " is-muted"}`}>
      <div className="row-main">
        <div className="row-title">
          <a id={`${uid}-name-${member.id}`} href={`#members?id=${member.id}`}>
            {member.full_name}
            <span className="sr-only"> – attendance history</span>
          </a>
          {!member.active && (
            <>
              {" "}
              <span className="chip">Inactive</span>
            </>
          )}
        </div>
        {sub.length > 0 && (
          <div className="row-sub">
            {sub.map((t) => (
              <span key={t}>{t}</span>
            ))}
          </div>
        )}
      </div>
      {names.length > 0 && (
        <div className="row-side">
          <span className="sr-only">Groups:</span>
          {names.map((n) => (
            <span key={n} className="chip accent">
              {n}
            </span>
          ))}
        </div>
      )}
      {canEdit && (
        <div className="row-actions-inline">
          <button type="button" id={`${uid}-edit-${member.id}`} className="small" onClick={() => onEdit(member)}>
            Edit<span className="sr-only"> {member.full_name}</span>
          </button>
        </div>
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
  const [groupsError, setGroupsError] = useState("");
  const [notice, setNotice] = useState("");
  const [version, setVersion] = useState(0);
  const [filtersOpen, setFiltersOpen] = useState(Boolean(groupParam)); // the inline panel (desktop)
  const [sheetOpen, setSheetOpen] = useState(false); // the Filters sheet (phones)
  const phone = useIsPhone();
  const groupsCtl = useRef(null);
  const headRef = useRef(null);
  const addRef = useRef(null);
  const returnTo = useRef(null); // member id (or "new") whose control gets focus when the form closes

  useEffect(() => {
    setGroup(groupParam ?? "");
    if (groupParam) setFiltersOpen(true);
  }, [groupParam]);

  // When the form closes, put focus back on the control that opened it (or the heading if that row is gone).
  useEffect(() => {
    if (editing !== null || returnTo.current === null) return;
    const rid = returnTo.current;
    returnTo.current = null;
    const el =
      rid === "new"
        ? addRef.current
        : (document.getElementById(`${id}-edit-${rid}`) ?? document.getElementById(`${id}-name-${rid}`));
    (el ?? headRef.current)?.focus();
  }, [editing, id]);

  const closeForm = () => {
    returnTo.current = editing === "new" ? "new" : (editing?.id ?? null);
    setEditing(null);
  };

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  const loadGroups = useCallback(() => {
    groupsCtl.current?.abort();
    const ctl = new AbortController();
    groupsCtl.current = ctl;
    swr("/groups", setGroups, { signal: ctl.signal })
      .then(() => setGroupsError(""))
      .catch((e) => !isAbort(e) && setGroupsError(errorText(e)));
  }, []);
  useEffect(() => {
    loadGroups();
    return () => groupsCtl.current?.abort();
  }, [loadGroups]);

  useEffect(() => {
    if (view !== "list") return undefined;
    const ctl = new AbortController();
    swr(
      `/members${query({ q: debouncedQ, group_id: group, active })}`,
      (m, { stale }) => {
        setMembers(m);
        if (!stale) setError("");
      },
      { signal: ctl.signal },
    ).catch((e) => !isAbort(e) && setError(errorText(e))); // a failed refresh keeps the cached list on screen
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

  const activeFilters = (group ? 1 : 0) + (active ? 1 : 0);
  const groupName = group ? groupNames.get(Number(group)) : null;
  const clearFilters = () => {
    setGroup("");
    setActive("");
  };
  const editingInList = Boolean(editing && editing !== "new" && members?.some((m) => m.id === editing.id));
  // Deactivate/Reactivate went through: close the form, say so, and offer to put it back.
  const toggled = (member, nowActive) => {
    closeForm();
    refresh();
    toast({
      text: `${nowActive ? "Reactivated" : "Deactivated"} ${member.full_name}.`,
      action: {
        label: "Undo",
        run: async () => {
          await api(`/members/${member.id}`, { method: "PATCH", body: { active: !nowActive } });
          refresh();
        },
      },
    });
  };
  const form = (member) => (
    <MemberForm
      key={member ? member.id : "new"}
      member={member}
      groups={groups}
      canDeactivate={role === "secretary"}
      onToggled={toggled}
      onCancel={closeForm}
      onSaved={() => {
        closeForm();
        refresh();
      }}
    />
  );

  return (
    <>
      <div className="page-head">
        <h1 tabIndex={-1} ref={headRef}>
          Members
        </h1>
        <div className="actions">
          {canEdit && !editing && (
            <button
              type="button"
              className="primary"
              ref={addRef}
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
              Import<span className="hide-narrow"> from spreadsheet</span>
            </button>
          )}
        </div>
      </div>
      {notice && (
        <p role="status" className="notice">
          {notice}
        </p>
      )}
      {editing && !editingInList && form(editing === "new" ? null : editing)}
      <div className="searchbar members-search">
        <label htmlFor={`${id}-q`} className="sr-only">
          Search members
        </label>
        <input
          id={`${id}-q`}
          type="search"
          enterKeyHint="search"
          autoComplete="off"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Name, email, phone or code"
        />
        <button
          type="button"
          className="filters-toggle"
          aria-expanded={phone ? sheetOpen : filtersOpen}
          aria-controls={phone ? undefined : `${id}-filters`}
          aria-haspopup={phone ? "dialog" : undefined}
          onClick={() => (phone ? setSheetOpen(true) : setFiltersOpen((o) => !o))}
        >
          Filters
          {activeFilters > 0 && (
            <>
              {" "}
              <span className="filters-count" aria-hidden="true">
                {activeFilters}
              </span>
              <span className="sr-only"> ({activeFilters} active)</span>
            </>
          )}
        </button>
        <span className="count" role="status">
          {members ? plural(members.length, "member", "members") : ""}
        </span>
        {groupName && (
          <button type="button" className="chip accent chip-remove" aria-label={`Remove group filter ${groupName}`} onClick={() => setGroup("")}>
            Group: {groupName} <span aria-hidden="true">✕</span>
          </button>
        )}
        {activeFilters > 0 && (
          <button type="button" className="text-button" onClick={clearFilters}>
            Clear filters
          </button>
        )}
      </div>
      {!phone && filtersOpen && (
        <div className="card filters filters-panel" id={`${id}-filters`}>
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
      )}
      <Sheet
        open={phone && sheetOpen}
        onClose={() => setSheetOpen(false)}
        title="Filters"
        footer={
          <>
            <button type="button" className="primary" onClick={() => setSheetOpen(false)}>
              {members ? `Show ${plural(members.length, "member", "members")}` : "Show members"}
            </button>
            {activeFilters > 0 && (
              <button type="button" onClick={clearFilters}>
                Clear filters
              </button>
            )}
          </>
        }
      >
        <div className="sheet-filters">
          <div className="field">
            <label htmlFor={`${id}-sgroup`}>Group</label>
            <select id={`${id}-sgroup`} value={group} onChange={(e) => setGroup(e.target.value)}>
              <option value="">All groups</option>
              {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`${id}-sactive`}>Show</label>
            <select id={`${id}-sactive`} value={active} onChange={(e) => setActive(e.target.value)}>
              <option value="">All members</option>
              <option value="true">Active only</option>
              <option value="false">Inactive only</option>
            </select>
          </div>
        </div>
      </Sheet>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {groupsError && (
        <p role="alert" className="error">
          Couldn't load the groups list. {groupsError}
        </p>
      )}
      {!members && !error && <SkeletonRows rows={6} label="Loading members" />}
      {members && members.length === 0 && <p className="empty">No members match. Change the search or filters, or add a member.</p>}
      {members && members.length > 0 && (
        <ul className="rows">
          {members.map((m) =>
            editingInList && editing.id === m.id ? (
              <li key={m.id} className="row row-form">
                {form(editing)}
              </li>
            ) : (
              <MemberRow
                key={m.id}
                member={m}
                groupNames={groupNames}
                canEdit={canEdit}
                uid={id}
                onEdit={(mem) => {
                  setNotice("");
                  setEditing(mem);
                }}
              />
            ),
          )}
        </ul>
      )}
      {members && (
        <p>
          <a className="button small" href={`/exports/members.csv${query({ q: debouncedQ, group_id: group, active })}`} download>
            Download this list (CSV)
          </a>
        </p>
      )}
    </>
  );
}

export default function Members({ role, groupParam, idParam }) {
  if (idParam) return <MemberHistory key={idParam} id={idParam} />;
  return <MemberList role={role} groupParam={groupParam} />;
}
