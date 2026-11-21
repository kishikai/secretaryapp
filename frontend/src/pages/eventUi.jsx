import { useIdempotency } from "./idempotency.js";
import { STATUS_LABELS } from "../format.js";

export function StatusChip({ status }) {
  const cls = status === "scheduled" ? "accent" : status === "completed" ? "ok" : status === "cancelled" ? "danger" : "";
  return <span className={`chip ${cls}`}>{STATUS_LABELS[status] ?? status}</span>;
}

export const goToEvent = (id) => {
  window.location.hash = id ? `events?id=${id}` : "events";
};

export const ATTENDANCE_LABELS = {
  not_recorded: "Not recorded",
  present: "Present",
  late: "Late",
  late_excused: "Late (excused)",
  absent: "Absent",
  absent_excused: "Absent (excused)",
  no_practice: "No practices",
};
// "excused" was retired; the change history may still contain it.
export const attendanceLabel = (s) => (s === "excused" ? "Excused" : (ATTENDANCE_LABELS[s] ?? "Not recorded"));

// One idempotency key per create action. key.get(inputs) hands out the same key for the same inputs, so a
// second tap after "Could not reach the server" can't make a second copy; key.renew() after the action succeeded.
// One Idempotency-Key per action (see ./idempotency.js); get(sig) reuses it while sig is unchanged.
export function useIdempotencyKey() {
  const idem = useIdempotency();
  return { get: (sig = "") => idem.keyFor(sig), renew: () => idem.done() };
}
