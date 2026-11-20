import { useEffect, useId, useState } from "react";
import { api, isAbort, query } from "../api.js";
import { errorText, formatRate, daysAgoLocal, plural, todayLocal } from "../format.js";

export default function Absences() {
  const id = useId();
  const [dateFrom, setDateFrom] = useState(() => daysAgoLocal(90));
  const [dateTo, setDateTo] = useState(() => todayLocal());
  const [group, setGroup] = useState("");
  const [active, setActive] = useState("");
  const [groups, setGroups] = useState([]);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api("/groups")
      .then(setGroups)
      .catch(() => {});
  }, []);

  useEffect(() => {
    const ctl = new AbortController();
    api(`/absences${query({ date_from: dateFrom, date_to: dateTo, group_id: group, active })}`, { signal: ctl.signal })
      .then((list) => {
        setRows(list);
        setError("");
      })
      .catch((e) => !isAbort(e) && setError(errorText(e)));
    return () => ctl.abort();
  }, [dateFrom, dateTo, group, active]);

  return (
    <>
      <h1>Absences</h1>
      <p className="rule">
        Counts only finalised registers of events that weren't cancelled. Absence rate = absent ÷ (present + late + late
        excused + absent); absent (excused) isn't counted.
      </p>
      <div className="toolbar">
        <a className="button" href={`/exports/absences.csv${query({ date_from: dateFrom, date_to: dateTo, group_id: group, active })}`} download>
          Download absence report
        </a>
      </div>
      <div className="card filters">
        <div className="field">
          <label htmlFor={`${id}-from`}>From date</label>
          <input id={`${id}-from`} type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor={`${id}-to`}>To date</label>
          <input id={`${id}-to`} type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
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
            <option value="true">Active members</option>
            <option value="false">Inactive members</option>
          </select>
        </div>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!rows && !error && <p className="muted">Loading…</p>}
      {rows && (
        <p className="muted" role="status">
          {plural(rows.length, "member", "members")}
        </p>
      )}
      {rows && rows.length === 0 && (
        <p className="empty">No finalised registers in this period. Change the dates or filters.</p>
      )}
      {rows && rows.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col" className="num">Events counted</th>
                <th scope="col" className="num">Present</th>
                <th scope="col" className="num">Late</th>
                <th scope="col" className="num">Late (excused)</th>
                <th scope="col" className="num">Absent</th>
                <th scope="col" className="num">Absent (excused)</th>
                <th scope="col" className="num">Absence rate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.member_id}>
                  <th scope="row">
                    <a href={`#members?id=${r.member_id}`}>{r.full_name}</a>
                    {!r.active && <span className="chip"> Inactive</span>}
                  </th>
                  <td className="num">{r.events}</td>
                  <td className="num">{r.present}</td>
                  <td className="num">{r.late}</td>
                  <td className="num">{r.late_excused}</td>
                  <td className="num">{r.absent}</td>
                  <td className="num">{r.absent_excused}</td>
                  <td className="num">{formatRate(r.absence_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
