import { STATUS_LABELS } from "../format.js";

export function StatusChip({ status }) {
  const cls = status === "scheduled" ? "accent" : status === "completed" ? "ok" : status === "cancelled" ? "danger" : "";
  return <span className={`chip ${cls}`}>{STATUS_LABELS[status] ?? status}</span>;
}

export const goToEvent = (id) => {
  window.location.hash = id ? `events?id=${id}` : "events";
};
