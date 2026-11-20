import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { errorText, formatEventWhen, formatRate } from "../format.js";
import { StatusChip } from "./eventUi.jsx";

const STATUS = {
  present: "Present",
  late: "Late",
  late_excused: "Late (excused)",
  absent: "Absent",
  absent_excused: "Absent (excused)",
};

export default function MemberHistory({ id }) {
  const headRef = useRef(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api(`/members/${encodeURIComponent(id)}/attendance`)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(errorText(e)));
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    if (data) headRef.current?.focus();
  }, [data === null]); // eslint-disable-line react-hooks/exhaustive-deps

  const back = (
    <p className="back-link">
      <a href="#members">← All members</a>
    </p>
  );

  if (!data) {
    return (
      <>
        {back}
        {error ? (
          <p role="alert" className="error">
            {error}
          </p>
        ) : (
          <p className="muted">Loading…</p>
        )}
      </>
    );
  }

  const { member, summary, rows } = data;
  return (
    <>
      {back}
      <div className="page-head">
        <div className="head-title">
          <h1 tabIndex={-1} ref={headRef}>
            {member.full_name}
          </h1>
          {!member.active && <span className="chip">Inactive</span>}
        </div>
        <div className="actions">
          <a className="button small" href={`/exports/members/${encodeURIComponent(member.id)}/attendance.csv`} download>
            Download history (CSV)
          </a>
        </div>
      </div>
      <ul className="stat-strip abs-stats">
        <li className="is-lead">
          <span className="v num">{formatRate(summary.absence_rate)}</span>
          <span className="k">Absence rate</span>
        </li>
        {[
          ["Events counted", summary.events],
          ["Present", summary.present],
          ["Late", summary.late],
          ["Late (excused)", summary.late_excused],
          ["Absent", summary.absent],
          ["Absent (excused)", summary.absent_excused],
        ].map(([label, value]) => (
          <li key={label}>
            <span className="v num">{value}</span>
            <span className="k">{label}</span>
          </li>
        ))}
      </ul>
      <details className="explain">
        <summary>How is this worked out?</summary>
        <p>
          Attendance history. Totals count only finalised registers of events that weren't cancelled. Absence rate =
          absent ÷ (present + late + late excused + absent); absent (excused) isn't counted.
        </p>
      </details>

      <h2>Events they were expected at</h2>
      {rows.length === 0 ? (
        <p className="empty">This member hasn't been expected at any events yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table history-table">
            <thead>
              <tr>
                <th scope="col">Event</th>
                <th scope="col">When</th>
                <th scope="col">Event status</th>
                <th scope="col">Register</th>
                <th scope="col">Their status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.event_id} className={r.counted ? undefined : "not-counted"}>
                  <th scope="row">
                    <a href={`#events?id=${r.event_id}`}>{r.name}</a>
                  </th>
                  <td className="when">{formatEventWhen(r.starts_at, r.ends_at)}</td>
                  <td>
                    <StatusChip status={r.event_status} />
                  </td>
                  <td>{r.finalized ? "Finalised" : "Not finalised"}</td>
                  <td>
                    {r.status ? <span className={`chip st-${r.status}`}>{STATUS[r.status] ?? r.status}</span> : "Not recorded"}
                    {!r.counted && <span className="chip"> Not counted</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
