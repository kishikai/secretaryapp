import { useEffect, useId, useMemo, useState } from "react";
import { api, isAbort, query } from "../api.js";
import { errorText, formatRate, daysAgoLocal, plural, todayLocal } from "../format.js";

// Column definitions: key in the API row, label, short label for phones, and the first-click direction.
const COLUMNS = [
  { key: "full_name", label: "Name", short: "Name", first: "ascending" },
  { key: "events", label: "Events counted", short: "Events", first: "descending", num: true },
  { key: "present", label: "Present", short: "Present", first: "descending", num: true },
  { key: "late", label: "Late", short: "Late", first: "descending", num: true },
  { key: "late_excused", label: "Late (excused)", short: "Late exc.", first: "descending", num: true },
  { key: "absent", label: "Absent", short: "Absent", first: "descending", num: true },
  { key: "absent_excused", label: "Absent (excused)", short: "Abs. exc.", first: "descending", num: true },
  { key: "absence_rate", label: "Absence rate", short: "Rate", first: "descending", num: true },
];

// Rows without a value (no rate yet) always go last, whichever way the column is sorted.
function sortRows(rows, sort) {
  if (!sort) return rows;
  const dir = sort.dir === "ascending" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = a[sort.key];
    const y = b[sort.key];
    if (x === null || x === undefined) return y === null || y === undefined ? 0 : 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === "string") return dir * x.localeCompare(y, undefined, { sensitivity: "base" });
    return dir * (x - y);
  });
}

export default function Absences() {
  const id = useId();
  const [dateFrom, setDateFrom] = useState(() => daysAgoLocal(90));
  const [dateTo, setDateTo] = useState(() => todayLocal());
  const [group, setGroup] = useState("");
  const [active, setActive] = useState("");
  const [groups, setGroups] = useState([]);
  const [rows, setRows] = useState(null);
  const [error, setError] = useState("");
  const [sort, setSort] = useState(null); // null = the order the API returned

  // First click sorts, second click reverses, third click goes back to the API's order.
  const clickSort = (col) =>
    setSort((cur) => {
      if (!cur || cur.key !== col.key) return { key: col.key, dir: col.first };
      if (cur.dir === col.first) return { key: col.key, dir: col.first === "ascending" ? "descending" : "ascending" };
      return null;
    });
  const sorted = useMemo(() => sortRows(rows ?? [], sort), [rows, sort]);

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
      <div className="page-head">
        <h1>Absences</h1>
      </div>
      <details className="explain">
        <summary>How is this worked out?</summary>
        <p>
          Counts only finalised registers of events that weren't cancelled. Absence rate = absent ÷ (present + late + late
          excused + absent); absent (excused) isn't counted.
        </p>
      </details>
      <div className="card filters abs-filters">
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
        <>
          <div className="table-wrap">
            <table className="data-table abs-table">
              <thead>
                <tr>
                  {COLUMNS.map((c) => {
                    const dir = sort?.key === c.key ? sort.dir : "none";
                    return (
                      <th key={c.key} scope="col" className={`sortable${c.num ? " num" : ""}`} aria-sort={dir}>
                        <button type="button" className="sort-btn" onClick={() => clickSort(c)}>
                          <span className={c.short !== c.label ? "lbl-full" : undefined}>{c.label}</span>
                          {c.short !== c.label && (
                            <span className="lbl-short" aria-hidden="true">
                              {c.short}
                            </span>
                          )}
                          <span className="sort-ind" aria-hidden="true">
                            {dir === "ascending" ? "▲" : dir === "descending" ? "▼" : ""}
                          </span>
                        </button>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {sorted.map((r) => (
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
          <p>
            <a
              className="button small"
              href={`/exports/absences.csv${query({ date_from: dateFrom, date_to: dateTo, group_id: group, active })}`}
              download
            >
              Download this report (CSV)
            </a>
          </p>
        </>
      )}
    </>
  );
}
