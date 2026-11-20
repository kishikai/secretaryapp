import { useEffect, useId, useState } from "react";
import { api } from "../api.js";
import { errorText, formatTime, plural } from "../format.js";

const STATUSES = ["present", "late", "late_excused", "absent", "absent_excused", "not_recorded"];
const LABELS = {
  not_recorded: "Not recorded",
  present: "Present",
  late: "Late",
  late_excused: "Late (excused)",
  absent: "Absent",
  absent_excused: "Absent (excused)",
};
const COUNT_ORDER = STATUSES;
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

function StatusPicker({ name, person, value, disabled, onChange }) {
  return (
    <fieldset className="status-pick">
      <legend className="sr-only">Attendance for {person}</legend>
      {STATUSES.map((s) => (
        <label key={s} className={`seg st-${s}${value === s ? " on" : ""}`}>
          <input type="radio" name={name} className="sr-only" value={s} checked={value === s} disabled={disabled} onChange={() => onChange(s)} />
          <span>{LABELS[s]}</span>
        </label>
      ))}
    </fieldset>
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
  const [search, setSearch] = useState("");
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
  const shown = needle ? attendees.filter((a) => a.full_name.toLowerCase().includes(needle)) : attendees;
  const editable = canRecord && !finalised;
  const notRecorded = counts.not_recorded;

  let finaliseWhy = "";
  if (attendees.length === 0) finaliseWhy = "Add people to the expected list first.";
  else if (unsaved > 0) finaliseWhy = "Save your progress before finalising.";
  else if (notRecorded > 0) finaliseWhy = `${plural(notRecorded, "person is", "people are")} still not recorded. Everyone needs a status before you can finalise.`;

  return (
    <section className="card" aria-labelledby={`${uid}-h`}>
      <h2 id={`${uid}-h`}>Register</h2>

      {finalised && (
        <p role="status" className="banner">
          Finalised by {event.finalized_by ?? "someone"} on {formatTime(event.finalized_at)}
        </p>
      )}

      <div className="toolbar">
        <a className="button" href={`/exports/events/${encodeURIComponent(event.id)}/register.csv`} download>
          Download attendance sheet
        </a>
        {unsaved > 0 && <small className="muted">Save first: the sheet shows saved statuses.</small>}
      </div>

      <ul className="register-counts" aria-label="Totals">
        {COUNT_ORDER.map((s) => (
          <li key={s} className={`chip st-${s}`}>
            {LABELS[s]}: {counts[s]}
          </li>
        ))}
      </ul>

      {attendees.length === 0 ? (
        <p className="empty">No one is on the expected list yet.</p>
      ) : (
        <>
          <div className="field">
            <label htmlFor={`${uid}-search`}>Search the register</label>
            <input id={`${uid}-search`} type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Type a name" />
          </div>
          {editable && (
            <div className="row-actions" style={{ marginTop: 0, marginBottom: "0.5rem" }}>
              <button type="button" disabled={busy || notRecorded === 0} onClick={markRestPresent}>
                Mark everyone not yet recorded as present
              </button>
            </div>
          )}
          {shown.length === 0 && <p className="empty">No one on the list matches that name.</p>}
          <ul className="attendees">
            {shown.map((a) => {
              const value = effective(a);
              const changed = editable && edits[a.member_id] !== undefined;
              return (
                <li key={a.member_id} className="attendee register-row">
                  <div className="who">
                    <strong>{a.full_name}</strong>
                    {!a.active && (
                      <>
                        {" "}
                        <span className="chip">Inactive</span>
                      </>
                    )}
                    {changed && <span className="muted"> · changed, not saved</span>}
                    {a.recorded_by && !changed && <div className="meta">recorded by {a.recorded_by}</div>}
                  </div>
                  {editable ? (
                    <StatusPicker name={`${uid}-${a.member_id}`} person={a.full_name} value={value} disabled={busy} onChange={(s) => setOne(a, s)} />
                  ) : (
                    <span className={`chip st-${value}`}>{label(value)}</span>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}

      {editable && attendees.length > 0 && (
        <>
          <div className="field">
            <label htmlFor={`${uid}-reason`}>Note for the history (optional)</label>
            <input id={`${uid}-reason`} type="text" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: late arrivals added from the sign-in sheet" />
          </div>
          <div className="row-actions">
            <button type="button" className="primary" disabled={busy || unsaved === 0} onClick={save}>
              {busy ? "Saving…" : "Save progress"}
            </button>
            <span role="status" className="muted">
              {unsaved > 0 ? `${plural(unsaved, "unsaved change", "unsaved changes")}` : ""}
            </span>
          </div>
        </>
      )}

      {isSecretary && !finalised && attendees.length > 0 && (
        <div className="finalise">
          {step === "finalize" ? (
            <>
              <p role="status">Finalising locks the register. Continue?</p>
              <div className="row-actions">
                <span className="confirm">
                  <button type="button" className="danger" disabled={busy} onClick={finalise}>
                    Yes, finalise
                  </button>
                  <button type="button" onClick={() => setStep(null)}>
                    Not yet
                  </button>
                </span>
              </div>
            </>
          ) : (
            <div className="row-actions">
              <button type="button" disabled={busy || Boolean(finaliseWhy)} aria-describedby={finaliseWhy ? `${uid}-why` : undefined} onClick={() => setStep("finalize")}>
                Finalise register
              </button>
              {finaliseWhy && (
                <span id={`${uid}-why`} className="muted">
                  {finaliseWhy}
                </span>
              )}
            </div>
          )}
        </div>
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

      <History eventId={event.id} refreshKey={refreshKey} />
    </section>
  );
}
