const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const dayLabel = new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" });

// Parse an API timestamp. Values without a zone designator are treated as UTC.
export function parseDate(value) {
  if (!value) return null;
  let s = String(value);
  if (/^\d{4}-\d\d-\d\d[T ]/.test(s) && !/(Z|[+-]\d\d:?\d\d)$/i.test(s)) s = s.replace(" ", "T") + "Z";
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

// Formatted in the viewer's time zone.
export function formatTime(value) {
  const d = parseDate(value);
  return d ? dateTime.format(d) : "";
}

// "YYYY-MM-DD" (a calendar day, not an instant) -> "Nov 5".
export function formatDay(day) {
  return dayLabel.format(new Date(`${day}T00:00:00Z`));
}

// Confidence is expected as a 0..1 fraction; values above 1 are taken as already a percentage.
export function formatConfidence(c) {
  if (c === null || c === undefined) return null;
  const pct = c <= 1 ? c * 100 : c;
  return `${Math.round(pct)}%`;
}

export const sourceLabel = (s) => (s === "ocr" ? "OCR" : s === "clipboard" ? "Clipboard" : s);
export const chatLabel = (id) => (id === null || id === undefined ? "No chat" : id);
export const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// Readable text for an API error. FastAPI sends validation problems (422) as a list of {loc, msg}.
export function errorText(e) {
  if (Array.isArray(e?.detail) && e.detail.length) {
    return e.detail
      .map((d) => {
        const field = Array.isArray(d.loc) ? d.loc.filter((p) => p !== "body" && typeof p === "string").join(" ") : "";
        return field ? `${field}: ${d.msg}` : d.msg;
      })
      .join("; ");
  }
  return e?.message || "Something went wrong";
}

// Event times are local wall-clock strings ("YYYY-MM-DDTHH:MM", no zone). They are split, never parsed as instants.
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function splitLocal(value) {
  const m = /^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)/.exec(value ?? "");
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const day = `${WEEKDAYS[new Date(Date.UTC(y, mo - 1, d)).getUTCDay()]} ${d} ${MONTHS[mo - 1]} ${y}`;
  return { date: `${m[1]}-${m[2]}-${m[3]}`, day, time: `${m[4]}:${m[5]}` };
}

// "Sat 6 Dec 2025, 18:30–20:00" (or both days when the event runs past midnight).
export function formatEventWhen(start, end) {
  const s = splitLocal(start);
  if (!s) return "No date set";
  const e = splitLocal(end);
  if (!e) return `${s.day}, ${s.time}`;
  if (e.date === s.date) return `${s.day}, ${s.time}–${e.time}`;
  return `${s.day}, ${s.time} – ${e.day}, ${e.time}`;
}

// Value for <input type="datetime-local">.
export const toLocalInput = (value) => (splitLocal(value) ? String(value).slice(0, 16).replace(" ", "T") : "");

// Today as "YYYY-MM-DD" in the viewer's own calendar.
export function todayLocal() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export const STATUS_LABELS = { draft: "Draft", scheduled: "Scheduled", completed: "Completed", cancelled: "Cancelled" };
