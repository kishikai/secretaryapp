import { useEffect, useId, useState } from "react";
import { api } from "../api.js";
import { errorText, formatTime, plural } from "../format.js";

const LABELS = {
  not_recorded: "Not recorded",
  present: "Present",
  late: "Late",
  late_excused: "Late (excused)",
  absent: "Absent",
  absent_excused: "Absent (excused)",
};
const COUNT_ORDER = ["not_recorded", "present", "late", "late_excused", "absent", "absent_excused"];
// "excused" was retired; the change history may still contain it.
const label = (s) => (s === "excused" ? "Excused" : (LABELS[s] ?? "Not recorded"));

function auditText(a) {
  if (a.action === "finalize") return "Register finalised";
  if (a.action === "reopen") return "Register reopened";
  return `${a.full_name ?? "Someone"}: ${label(a.old_status)} → ${label(a.new_status)}`;
}

function History({ eventId, refreshKey }) {
  const [open, setOpen] = useState(false);
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    api(`/events/${eventId}/audit`)
      .then((rows) => {
        if (cancelled) return;
        setEntries(rows);
        setError("");
      })
      .catch((e) => !cancelled && setError(errorText(e)));
    return () => {
      cancelled = true;
    };
  }, [open, eventId, refreshKey]);

  return (
    <details className="register-history" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>Change history</summary>
      {open && error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {open && !entries && !error && <p className="muted">Loading…</p>}
      {entries && entries.length === 0 && <p className="empty">No changes have been recorded yet.</p>}
      {entries && entries.length > 0 && (
        <ul className="history-list">
          {entries.map((a) => (
            <li key={a.id}>
              <div>{auditText(a)}</div>
              <div className="meta">
                {formatTime(a.at)} · {a.actor}
              </div>
              {a.reason && <div className="text">Note: {a.reason}</div>}
            </li>
          ))}
        </ul>
      )}
    </details>
  );
}

// Three main buttons plus an "Excused" tick map onto the six API statuses.
const MAIN = [
  { key: "present", text: "Present" },
  { key: "late", text: "Late" },
  { key: "absent", text: "Absent" },
];
const mainOf = (s) => (s === "present" ? "present" : s === "late" || s === "late_excused" ? "late" : s === "absent" || s === "absent_excused" ? "absent" : null);
const isExcused = (s) => s === "late_excused" || s === "absent_excused";

function AttendanceControls({ person, value, disabled, onChange }) {
  const main = mainOf(value);
  const excused = isExcused(value);
  const choose = (key) => {
    if (main === key) onChange("not_recorded");
    else if (key === "present") onChange("present");
    else onChange(excused ? `${key}_excused` : key);
  };
  const canExcuse = (main === "late" || main === "absent") && !disabled;
  return (
    <div className="reg-controls" role="group" aria-label={`Attendance for ${person}`}>
      {MAIN.map((m) => (
        <button key={m.key} type="button" className={`reg-btn st-${m.key}`} aria-pressed={main === m.key} disabled={disabled} onClick={() => choose(m.key)}>
          {m.text}
        </button>
      ))}
      <label className={`reg-excused${canExcuse ? "" : " is-off"}`}>
        <input type="checkbox" checked={excused} disabled={!canExcuse} onChange={(e) => onChange(e.target.checked ? `${main}_excused` : main)} />
        <span>
          Excused<span className="sr-only"> ({person})</span>
        </span>
      </label>
    </div>
  );
}

export default function Register({ event, role, onChanged }) {
  const uid = useId();
  const canRecord = role === "secretary" || role === "assistant";
  const isSecretary = role === "secretary";
  const finalised = Boolean(event.finalized_at);
  const attendees = event.attendees ?? [];

  const [edits, setEdits] = useState({}); // member_id -> status, only where it differs from the saved value
  const [reason, setReason] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState(null); // one status, or null for everyone
  const [step, setStep] = useState(null); // "finalize" | "reopen" | null
  const [reopenReason, setReopenReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  const saved = (a) => a.status ?? "not_recorded";
  const effective = (a) => (finalised ? saved(a) : (edits[a.member_id] ?? saved(a)));
  const changes = attendees.filter((a) => edits[a.member_id] !== undefined && edits[a.member_id] !== saved(a));
  const unsaved = changes.length;

  // While there is unsaved work, warn before the page closes and before any in-app link (tabs, "All events", day links) leaves it.
  useEffect(() => {
    if (unsaved === 0) return undefined;
    const onUnload = (e) => {
      e.preventDefault();
      e.returnValue = "";
    };
    const onClick = (e) => {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      const a = e.target instanceof Element ? e.target.closest('a[href^="#"]') : null;
      if (!a || a.classList.contains("skip-link") || a.getAttribute("href") === window.location.hash) return;
      if (!window.confirm(`You have ${plural(unsaved, "unsaved attendance change", "unsaved attendance changes")}. Leave without saving?`)) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", onUnload);
    document.addEventListener("click", onClick, true);
    return () => {
      window.removeEventListener("beforeunload", onUnload);
      document.removeEventListener("click", onClick, true);
    };
  }, [unsaved]);

  const counts = Object.fromEntries(COUNT_ORDER.map((s) => [s, 0]));
  for (const a of attendees) counts[effective(a)] = (counts[effective(a)] ?? 0) + 1;

  const setOne = (a, status) => {
    setNotice("");
    setEdits((cur) => {
      const next = { ...cur };
      if (status === saved(a)) delete next[a.member_id];
      else next[a.member_id] = status;
      return next;
    });
  };

  const markRestPresent = () => {
    setNotice("");
    setEdits((cur) => {
      const next = { ...cur };
      for (const a of attendees) {
        if ((next[a.member_id] ?? saved(a)) === "not_recorded") next[a.member_id] = "present";
      }
      return next;
    });
  };

  // Runs a request, then reloads the event and resets everything typed locally.
  const run = async (request, doneText) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await request();
      setEdits({});
      setReason("");
      setNoteOpen(false);
      setReopenReason("");
      setStep(null);
      await onChanged();
      setRefreshKey((k) => k + 1);
      setNotice(doneText);
    } catch (e) {
      setError(errorText(e));
      if (step !== "reopen") setStep(null);
    } finally {
      setBusy(false);
    }
  };

  const save = () =>
    run(() => {
      const body = { records: changes.map((a) => ({ member_id: a.member_id, status: edits[a.member_id] })) };
      if (reason.trim()) body.reason = reason.trim();
      return api(`/events/${event.id}/attendance`, { method: "PUT", body });
    }, "Progress saved.");
  const finalise = () => run(() => api(`/events/${event.id}/finalize`, { method: "POST" }), "Register finalised.");
  const reopen = () =>
    run(() => api(`/events/${event.id}/reopen`, { method: "POST", body: { reason: reopenReason.trim() } }), "Register reopened. You can change it again.");

  const needle = search.trim().toLowerCase();
  // A row with an unsaved edit stays visible so it doesn't vanish the moment it is marked.
  const shown = attendees.filter(
    (a) =>
      (!needle || a.full_name.toLowerCase().includes(needle)) &&
      (!statusFilter || effective(a) === statusFilter || (!finalised && edits[a.member_id] !== undefined)),
  );
  const filterName = statusFilter ? label(statusFilter).toLowerCase() : "";
  const noneText = !statusFilter ? "No one on the list matches that name." : needle ? "No one matches." : `No one is ${filterName}.`;
  const editable = canRecord && !finalised;
  const notRecorded = counts.not_recorded;
  const showBar = editable && attendees.length > 0;

  let finaliseWhy = "";
  if (attendees.length === 0) finaliseWhy = "Add people to the expected list first.";
  else if (unsaved > 0) finaliseWhy = "Save your changes first.";
  else if (notRecorded > 0) finaliseWhy = `${plural(notRecorded, "person is", "people are")} not recorded yet.`;

  const statusText =
    unsaved > 0 ? plural(unsaved, "unsaved change", "unsaved changes") : notice || (isSecretary && !finaliseWhy ? "Ready to finalise" : "All changes saved");

  const errorEl = error && (
    <p role="alert" className="error reg-msg">
      {error}
    </p>
  );

  return (
    <section className="card register-card" aria-labelledby={`${uid}-h`}>
      <h2 id={`${uid}-h`}>Register</h2>

      {finalised && (
        <p role="status" className="banner">
          Finalised by {event.finalized_by ?? "someone"} on {formatTime(event.finalized_at)}
        </p>
      )}

      {attendees.length > 0 && (
        <div className="reg-tools">
          <input
            id={`${uid}-search`}
            aria-label="Search the register"
            type="search"
            enterKeyHint="search"
            autoComplete="off"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Find a name"
          />
          {editable && (
            <button type="button" disabled={busy || notRecorded === 0} onClick={markRestPresent}>
              Mark the rest present
            </button>
          )}
        </div>
      )}

      <ul className="register-counts" aria-label="Totals">
        {COUNT_ORDER.map((s) => (
          <li key={s}>
            <button type="button" className={`chip chip-toggle st-${s}`} aria-pressed={statusFilter === s} onClick={() => setStatusFilter((cur) => (cur === s ? null : s))}>
              {LABELS[s]}: {counts[s]}
            </button>
          </li>
        ))}
      </ul>

      {attendees.length === 0 ? (
        <p className="empty">No one is on the expected list yet.</p>
      ) : (
        <>
          {statusFilter && (
            <p role="status" className="muted filter-line">
              Showing {shown.length} {filterName} ·{" "}
              <button type="button" className="text-button" onClick={() => setStatusFilter(null)}>
                Show everyone
              </button>
            </p>
          )}
          {shown.length === 0 && <p className="empty">{noneText}</p>}
          <ul className="reg-list">
            {shown.map((a) => {
              const value = effective(a);
              const changed = editable && edits[a.member_id] !== undefined;
              return (
                <li key={a.member_id} className={`reg-row${editable ? " is-edit" : ""}`}>
                  <div className="reg-who">
                    <strong>{a.full_name}</strong>
                    {!a.active && <span className="chip">Inactive</span>}
                    {changed && <span className="reg-unsaved">unsaved</span>}
                  </div>
                  {editable ? (
                    <AttendanceControls person={a.full_name} value={value} disabled={busy} onChange={(s) => setOne(a, s)} />
                  ) : (
                    <span className={`chip st-${value}`}>{label(value)}</span>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}

      {isSecretary && finalised && (
        <div className="finalise">
          {step === "reopen" ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (reopenReason.trim()) reopen();
              }}
            >
              <div className="field">
                <label htmlFor={`${uid}-reopen`}>Why are you reopening the register?</label>
                <textarea id={`${uid}-reopen`} rows={2} required maxLength={500} value={reopenReason} onChange={(e) => setReopenReason(e.target.value)} />
              </div>
              <div className="row-actions">
                <button type="submit" className="primary" disabled={busy || !reopenReason.trim()}>
                  {busy ? "Reopening…" : "Reopen register"}
                </button>
                <button type="button" onClick={() => setStep(null)}>
                  Keep it finalised
                </button>
              </div>
            </form>
          ) : (
            <div className="row-actions">
              <button type="button" disabled={busy} onClick={() => setStep("reopen")}>
                Reopen register
              </button>
            </div>
          )}
        </div>
      )}

      {!showBar && notice && (
        <p role="status" className="notice reg-msg">
          {notice}
        </p>
      )}
      {!showBar && errorEl}

      <div className="reg-foot">
        <History eventId={event.id} refreshKey={refreshKey} />
        <a className="button small" href={`/exports/events/${encodeURIComponent(event.id)}/register.csv`} download>
          Download attendance sheet
        </a>
        {unsaved > 0 && <small className="muted reg-hint">Save first: the sheet shows saved statuses.</small>}
      </div>

      {showBar && (
        <div className="savebar reg-savebar">
          {step === "finalize" ? (
            <>
              <p role="status" className="reg-status">
                Finalising locks the register. Continue?
              </p>
              <span className="confirm reg-buttons">
                <button type="button" className="danger" disabled={busy} onClick={finalise}>
                  Yes, finalise
                </button>
                <button type="button" onClick={() => setStep(null)}>
                  Not yet
                </button>
              </span>
            </>
          ) : (
            <>
              <div className="reg-top">
                <div className="reg-status-wrap">
                  <p role="status" className="reg-status">
                    {statusText}
                  </p>
                  {isSecretary && finaliseWhy && (
                    <p id={`${uid}-why`} className="muted reg-why">
                      {finaliseWhy}
                    </p>
                  )}
                </div>
                <button type="button" className="text-button reg-note-toggle" aria-expanded={noteOpen} aria-controls={`${uid}-note`} onClick={() => setNoteOpen((o) => !o)}>
                  {noteOpen ? "Hide note" : reason ? "Edit note" : "Add a note"}
                </button>
              </div>
              <div className="reg-buttons">
                <button type="button" className="primary" disabled={busy || unsaved === 0} onClick={save}>
                  {busy ? "Saving…" : "Save"}
                </button>
                {isSecretary && (
                  <button type="button" disabled={busy || Boolean(finaliseWhy)} aria-describedby={finaliseWhy ? `${uid}-why` : undefined} onClick={() => setStep("finalize")}>
                    Finalise register
                  </button>
                )}
              </div>
              {noteOpen && (
                <input
                  id={`${uid}-note`}
                  className="reg-note"
                  type="text"
                  aria-label="Note for the history (optional)"
                  maxLength={500}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Note for the history (optional)"
                />
              )}
            </>
          )}
          {errorEl}
        </div>
      )}
    </section>
  );
}
