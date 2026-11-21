import { useEffect, useId, useRef, useState } from "react";
import { api } from "../api.js";
import { errorText, plural } from "../format.js";
import { useIdempotency } from "./idempotency.js";

// Per-row choice is stored as "create", "skip" or "update:<member id>".
function defaultChoice(row) {
  if (row.errors.length > 0) return "skip";
  if (row.duplicates.length > 0) return "skip";
  return "create";
}

function updateTargets(row) {
  const seen = new Map();
  for (const d of row.duplicates) {
    if (d.member_id != null && !seen.has(d.member_id)) seen.set(d.member_id, d.full_name);
  }
  return [...seen];
}

function toPayload(row, choice) {
  if (choice.startsWith("update:")) return { data: row.data, action: "update", member_id: Number(choice.slice(7)) };
  return { data: row.data, action: choice, member_id: null };
}

export default function MemberImport({ onCancel, onDone }) {
  const id = useId();
  const headRef = useRef(null);
  const [file, setFile] = useState(null);
  const [rows, setRows] = useState(null); // preview rows, null until a file has been checked
  const [choices, setChoices] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const idem = useIdempotency();

  useEffect(() => {
    headRef.current?.focus();
  }, []);

  const preview = async (e) => {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    const form = new FormData();
    form.append("file", file);
    try {
      const r = await api("/members/import/preview", { method: "POST", form });
      setRows(r.rows);
      setChoices(Object.fromEntries(r.rows.map((row) => [row.row, defaultChoice(row)])));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    setBusy(true);
    setError("");
    try {
      const body = { rows: rows.map((row) => toPayload(row, choices[row.row])) };
      const result = await api("/members/import/confirm", { method: "POST", body, idempotencyKey: idem.keyFor(body) });
      idem.done();
      onDone(result);
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  const counts = { create: 0, update: 0, skip: 0 };
  if (rows) {
    for (const row of rows) {
      const c = choices[row.row] ?? "skip";
      counts[c.startsWith("update:") ? "update" : c] += 1;
    }
  }

  return (
    <>
      <div className="page-head">
        <h1 tabIndex={-1} ref={headRef}>
          Import members
        </h1>
      </div>
      <p>
        Save your spreadsheet as CSV (UTF-8). Columns: full_name (or name), email, phone, member_code, notes,
        joined_on, left_on, groups (separate several with ;).
      </p>

      {!rows && (
        <form className="card" onSubmit={preview}>
          <div className="field upload">
            <label htmlFor={`${id}-file`}>CSV file</label>
            <input
              id={`${id}-file`}
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </div>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <div className="row-actions">
            <button type="submit" className="primary" disabled={!file || busy}>
              {busy ? "Checking…" : "Check file"}
            </button>
            <button type="button" onClick={onCancel}>
              Cancel
            </button>
          </div>
        </form>
      )}

      {rows && (
        <>
          <p className="muted">
            Nothing is saved until you confirm. Rows with problems are skipped. Rows that look like an existing
            member are skipped unless you choose otherwise.
          </p>
          {rows.length === 0 && <p className="empty">The file has no rows.</p>}
          {rows.length > 0 && (
            <div className="table-wrap">
              <table className="data-table">
                <caption className="sr-only">Rows found in the file</caption>
                <thead>
                  <tr>
                    <th scope="col">Row</th>
                    <th scope="col">Name</th>
                    <th scope="col">Email</th>
                    <th scope="col">Phone</th>
                    <th scope="col">Groups</th>
                    <th scope="col">Problems</th>
                    <th scope="col">Possible matches</th>
                    <th scope="col">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const targets = updateTargets(row);
                    const hasErrors = row.errors.length > 0;
                    return (
                      <tr key={row.row}>
                        <th scope="row">{row.row}</th>
                        <td>{row.data.full_name}</td>
                        <td>{row.data.email}</td>
                        <td>{row.data.phone}</td>
                        <td>{[].concat(row.data.groups ?? []).join("; ")}</td>
                        <td>
                          {row.errors.map((er) => (
                            <div key={er} className="cell-error">
                              {er}
                            </div>
                          ))}
                        </td>
                        <td>
                          {row.duplicates.map((d, i) => (
                            <div key={i}>
                              {d.full_name ? `${d.full_name}: ` : ""}
                              {d.reason}
                            </div>
                          ))}
                        </td>
                        <td>
                          <select
                            aria-label={`Action for row ${row.row}`}
                            value={choices[row.row]}
                            disabled={busy || hasErrors}
                            onChange={(e) => setChoices((cur) => ({ ...cur, [row.row]: e.target.value }))}
                          >
                            {!hasErrors && <option value="create">Create</option>}
                            {!hasErrors &&
                              targets.map(([mid, name]) => (
                                <option key={mid} value={`update:${mid}`}>
                                  Update existing{name ? `: ${name}` : ""}
                                </option>
                              ))}
                            <option value="skip">Skip</option>
                          </select>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <p role="status">
            <strong>{plural(counts.create, "member", "members")}</strong> will be created,{" "}
            <strong>{counts.update}</strong> updated and <strong>{counts.skip}</strong> skipped.
          </p>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <div className="row-actions">
            <button type="button" className="primary" onClick={confirm} disabled={busy || counts.create + counts.update === 0}>
              {busy ? "Importing…" : "Confirm import"}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setRows(null);
                setFile(null);
                setError("");
              }}
            >
              Choose a different file
            </button>
            <button type="button" disabled={busy} onClick={onCancel}>
              Cancel
            </button>
          </div>
        </>
      )}
    </>
  );
}
