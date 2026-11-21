import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api } from "../api.js";
import { errorText, formatTime, plural } from "../format.js";
import Sheet from "../Sheet.jsx";
import { ATTENDANCE_LABELS as LABELS, attendanceLabel as label } from "./eventUi.jsx";

const COUNT_ORDER = ["not_recorded", "present", "late", "late_excused", "absent", "absent_excused", "no_practice"];

// ----- Unsaved marks kept on this device (the shell clears this prefix on sign-out) -----
const DRAFT_PREFIX = "secretary.registerDraft.";
const draftKey = (eventId) => `${DRAFT_PREFIX}${eventId}`;
const VALID_STATUS = new Set(COUNT_ORDER);

function readDraft(eventId) {
  try {
    const d = JSON.parse(localStorage.getItem(draftKey(eventId)) ?? "null");
    if (!d || typeof d.edits !== "object" || d.edits === null) return null;
    const edits = {};
    for (const [id, st] of Object.entries(d.edits)) if (VALID_STATUS.has(st)) edits[id] = st;
    return { edits, reason: typeof d.reason === "string" ? d.reason : "", at: typeof d.at === "string" ? d.at : "" };
  } catch {
    return null;
  }
}
function writeDraft(eventId, draft) {
  try {
    localStorage.setItem(draftKey(eventId), JSON.stringify(draft));
  } catch {
    // storage full or unavailable: the marks just aren't kept
  }
}
function removeDraft(eventId) {
  try {
    localStorage.removeItem(draftKey(eventId));
  } catch {
    // ignore
  }
}

// Keyboard shortcuts must never fire while typing.
const isTyping = (el) =>
  el instanceof Element &&
  (el.closest("textarea, select, [contenteditable]:not([contenteditable='false'])") !== null ||
    (el.tagName === "INPUT" && !["checkbox", "radio", "button", "submit", "reset"].includes(el.type)));

const SHORTCUTS = [
  [["↑", "↓"], "Previous or next person (j and k also work)"],
  [["p"], "Present"],
  [["l"], "Late"],
  [["a"], "Absent"],
  [["n"], "No practices"],
  [["e"], "Toggle excused (for late or absent)"],
  [["x"], "Clear (not recorded); Delete or Backspace also work"],
  [["Ctrl", "S"], "Save (Cmd + S on a Mac)"],
  [["?"], "Show this list"],
];

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

// Attendance buttons plus an "Excused" tick map onto the API statuses.
const MAIN = [
  { key: "present", text: "Present" },
  { key: "late", text: "Late" },
  { key: "absent", text: "Absent" },
  { key: "no_practice", text: "No practices" },
];
const mainOf = (s) => (s === "present" ? "present" : s === "late" || s === "late_excused" ? "late" : s === "absent" || s === "absent_excused" ? "absent" : s === "no_practice" ? "no_practice" : null);
const isExcused = (s) => s === "late_excused" || s === "absent_excused";

function AttendanceControls({ person, value, disabled, onChange }) {
  const main = mainOf(value);
  const excused = isExcused(value);
  const choose = (key) => {
    if (main === key) onChange("not_recorded");
    else if (key === "present") onChange("present");
    else if (key === "no_practice") onChange("no_practice");
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
  const [draft, setDraft] = useState(() => readDraft(event.id)); // marks kept on this device from an earlier visit
  const [helpOpen, setHelpOpen] = useState(false);
  const headRef = useRef(null);
  const pendingDraft = useRef(null);
  const holdRef = useRef(false);
  const prevUnsaved = useRef(0);

  const saved = (a) => a.status ?? "not_recorded";
  const effective = (a) => (finalised ? saved(a) : (edits[a.member_id] ?? saved(a)));
  const changes = attendees.filter((a) => edits[a.member_id] !== undefined && edits[a.member_id] !== saved(a));
  const unsaved = changes.length;
  const editable = canRecord && !finalised;

  // Drafted marks that still mean something: the person is on the list and the mark differs from the saved one.
  const draftMarks = {};
  if (draft && editable) {
    for (const a of attendees) {
      const d = draft.edits[a.member_id];
      if (d !== undefined && d !== saved(a)) draftMarks[a.member_id] = d;
    }
  }
  const draftCount = Object.keys(draftMarks).length;
  const hold = draftCount > 0; // while the notice waits for an answer, the stored draft isn't overwritten
  holdRef.current = hold;
  const snapshot = JSON.stringify(Object.fromEntries(changes.map((a) => [a.member_id, edits[a.member_id]])));

  // The key used by the effects below
  useEffect(() => {
    setDraft(readDraft(event.id));
  }, [event.id]);

  const dropStored = useCallback(() => {
    pendingDraft.current = null;
    removeDraft(event.id);
  }, [event.id]);
  const flushDraft = useCallback(() => {
    const p = pendingDraft.current;
    pendingDraft.current = null;
    if (p) writeDraft(p.id, p.data);
  }, []);

  // Keep unsaved marks on the device (debounced), so a dropped connection or a closed tab doesn't lose them.
  useEffect(() => {
    if (unsaved === 0 || finalised || hold) {
      pendingDraft.current = null;
      return undefined;
    }
    pendingDraft.current = { id: event.id, data: { edits: JSON.parse(snapshot), reason, at: new Date().toISOString() } };
    const t = setTimeout(flushDraft, 300);
    return () => clearTimeout(t);
  }, [snapshot, reason, unsaved, finalised, hold, event.id, flushDraft]);
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState === "hidden") flushDraft();
    };
    window.addEventListener("pagehide", flushDraft);
    document.addEventListener("visibilitychange", onHide);
    return () => {
      window.removeEventListener("pagehide", flushDraft);
      document.removeEventListener("visibilitychange", onHide);
    };
  }, [flushDraft]);
  // Back to nothing unsaved (saved, or every mark undone): the stored copy is no longer needed.
  useEffect(() => {
    if (prevUnsaved.current > 0 && unsaved === 0 && !holdRef.current) dropStored();
    prevUnsaved.current = unsaved;
  }, [unsaved, dropStored]);
  // A finalised register has nothing left to save.
  useEffect(() => {
    if (finalised) {
      dropStored();
      setDraft(null);
    }
  }, [finalised, dropStored]);

  const restoreDraft = () => {
    setNotice("");
    setEdits((cur) => ({ ...draftMarks, ...cur })); // marks made since then win
    if (draft.reason) setReason((r) => r || draft.reason);
    setDraft(null);
    headRef.current?.focus();
  };
  const discardDraft = () => {
    removeDraft(event.id);
    setDraft(null);
    headRef.current?.focus();
  };

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
      if (!holdRef.current) dropStored();
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

  // ----- Keyboard shortcuts (desktop) -----
  // Ctrl/Cmd+S and "?" work anywhere on the page while the register is editable; the rest only inside the list.
  const saveRef = useRef(save);
  saveRef.current = save;
  const canSave = editable && unsaved > 0 && !busy;
  useEffect(() => {
    if (!editable || attendees.length === 0) return undefined;
    const onKey = (e) => {
      if (e.isComposing) return;
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "s") {
        if (canSave) {
          e.preventDefault();
          saveRef.current();
        }
        return;
      }
      if (e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey && !isTyping(e.target) && !document.querySelector("dialog[open]")) {
        e.preventDefault();
        setHelpOpen(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [editable, attendees.length, canSave]);

  const onListKeyDown = (e) => {
    if (!editable || busy || e.isComposing || e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return;
    const row = e.target instanceof Element ? e.target.closest("[data-member]") : null;
    if (!row) return;
    const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
    if (key === "ArrowDown" || key === "ArrowUp" || key === "j" || key === "k") {
      const rows = [...e.currentTarget.querySelectorAll("[data-member]")];
      const other = rows[rows.indexOf(row) + (key === "ArrowDown" || key === "j" ? 1 : -1)];
      e.preventDefault();
      if (other) (other.querySelector('.reg-btn[aria-pressed="true"]') ?? other.querySelector(".reg-btn"))?.focus();
      return;
    }
    const a = attendees.find((x) => String(x.member_id) === row.dataset.member);
    if (!a) return;
    const value = effective(a);
    const excused = isExcused(value);
    const main = mainOf(value);
    let next = null;
    if (key === "p") next = "present";
    else if (key === "l") next = excused ? "late_excused" : "late";
    else if (key === "a") next = excused ? "absent_excused" : "absent";
    else if (key === "n") next = "no_practice";
    else if (key === "e") next = main === "late" || main === "absent" ? (excused ? main : `${main}_excused`) : undefined;
    else if (key === "x" || key === "Delete" || key === "Backspace") next = "not_recorded";
    else return;
    e.preventDefault();
    if (next && next !== value) setOne(a, next);
  };

  const needle = search.trim().toLowerCase();
  // A row with an unsaved edit stays visible so it doesn't vanish the moment it is marked.
  const shown = attendees.filter(
    (a) =>
      (!needle || a.full_name.toLowerCase().includes(needle)) &&
      (!statusFilter || effective(a) === statusFilter || (!finalised && edits[a.member_id] !== undefined)),
  );
  const filterName = statusFilter ? label(statusFilter).toLowerCase() : "";
  const noneText = !statusFilter ? "No one on the list matches that name." : needle ? "No one matches." : `No one is ${filterName}.`;
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
      <div className="reg-head">
        <h2 id={`${uid}-h`} tabIndex={-1} ref={headRef}>
          Register
        </h2>
        {editable && attendees.length > 0 && (
          <button type="button" className="text-button reg-shortcuts-btn" aria-haspopup="dialog" onClick={() => setHelpOpen(true)}>
            Keyboard shortcuts
          </button>
        )}
      </div>

      {draftCount > 0 && (
        <div role="status" className="notice reg-draft">
          <p>
            You have {plural(draftCount, "unsaved mark", "unsaved marks")} from {formatTime(draft.at) || "an earlier visit"} on this device.
          </p>
          <span className="reg-draft-actions">
            <button type="button" className="primary" onClick={restoreDraft}>
              Restore
            </button>
            <button type="button" onClick={discardDraft}>
              Discard
            </button>
          </span>
        </div>
      )}

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
          <ul className="reg-list" onKeyDown={onListKeyDown}>
            {shown.map((a) => {
              const value = effective(a);
              const changed = editable && edits[a.member_id] !== undefined;
              return (
                <li key={a.member_id} data-member={a.member_id} className={`reg-row${editable ? " is-edit" : ""}`}>
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

      <Sheet open={helpOpen} onClose={() => setHelpOpen(false)} title="Keyboard shortcuts">
        <p className="muted kbd-intro">Click or Tab into the list of people first. Shortcuts don&rsquo;t work while you are typing in a box.</p>
        <dl className="kbd-list">
          {SHORTCUTS.map(([keys, what]) => (
            <div key={what}>
              <dt>
                {keys.map((k, i) => (
                  <span key={k}>
                    {i > 0 && (keys[0] === "Ctrl" ? " + " : " ")}
                    <kbd>{k}</kbd>
                  </span>
                ))}
              </dt>
              <dd>{what}</dd>
            </div>
          ))}
        </dl>
      </Sheet>
    </section>
  );
}
