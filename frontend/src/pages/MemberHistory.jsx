import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { errorText, formatEventWhen, formatRate } from "../format.js";
import { StatusChip } from "./eventUi.jsx";

const STATUS = { present: "Present", late: "Late", absent: "Absent", excused: "Excused" };

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
    <p>
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
      <div className="item-head">
        <h1 tabIndex={-1} ref={headRef} style={{ margin: 0 }}>
          {member.full_name}
        </h1>
        {!member.active && <span className="chip">Inactive</span>}
      </div>
      <p className="rule">
        Attendance history. Totals count only finalised registers of events that weren't cancelled. Absence rate =
        absent ÷ (present + late + absent); excused isn't counted.
      </p>
      <div className="tiles">
        <div className="tile">
          <div className="tile-label">Events counted</div>
          <div className="tile-value num">{summary.events}</div>
        </div>
        <div className="tile">
          <div className="tile-label">Present</div>
          <div className="tile-value num">{summary.present}</div>
        </div>
        <div className="tile">
          <div className="tile-label">Late</div>
          <div className="tile-value num">{summary.late}</div>
        </div>
        <div className="tile">
          <div className="tile-label">Absent</div>
          <div className="tile-value num">{summary.absent}</div>
        </div>
        <div className="tile">
          <div className="tile-label">Excused</div>
          <div className="tile-value num">{summary.excused}</div>
        </div>
        <div className="tile">
          <div className="tile-label">Absence rate</div>
          <div className="tile-value num">{formatRate(summary.absence_rate)}</div>
        </div>
      </div>

      <h2>Events they were expected at</h2>
      {rows.length === 0 ? (
        <p className="empty">This member hasn't been expected at any events yet.</p>
      ) : (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Event</th>
                <th scope="col">Event status</th>
                <th scope="col">Register</th>
                <th scope="col">Their status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.event_id} className={r.counted ? undefined : "not-counted"}>
                  <td>{formatEventWhen(r.starts_at, r.ends_at)}</td>
                  <th scope="row">
                    <a href={`#events?id=${r.event_id}`}>{r.name}</a>
                  </th>
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
