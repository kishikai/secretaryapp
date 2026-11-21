import { useMemo, useRef } from "react";
import { newIdempotencyKey } from "../api.js";
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
};
// "excused" was retired; the change history may still contain it.
export const attendanceLabel = (s) => (s === "excused" ? "Excused" : (ATTENDANCE_LABELS[s] ?? "Not recorded"));

// One idempotency key per create action. key.get(inputs) hands out the same key for the same inputs, so a
// second tap after "Could not reach the server" can't make a second copy; key.renew() after the action succeeded.
export function useIdempotencyKey() {
  const state = useRef({ key: null, sig: null });
  return useMemo(
    () => ({
      get(sig = "") {
        const s = state.current;
        if (!s.key || s.sig !== sig) {
          s.key = newIdempotencyKey();
          s.sig = sig;
        }
        return s.key;
      },
      renew() {
        state.current = { key: null, sig: null };
      },
    }),
    [],
  );
}
