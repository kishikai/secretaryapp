// Shared placeholders and small hooks.
import { useEffect, useState } from "react";

// Grey rows shaped like the list that is loading. They fade in only after 150ms, so a fast load
// never flashes them; screen readers hear the label once.
export function SkeletonRows({ rows = 5, label = "Loading", card = true }) {
  return (
    <div className={`skeleton${card ? " skeleton-card" : ""}`} role="status">
      <span className="sr-only">{label}…</span>
      <div aria-hidden="true">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="sk-row">
            <span className="sk sk-title" />
            <span className="sk sk-sub" />
          </div>
        ))}
      </div>
    </div>
  );
}

// A few bars for a block of text or figures (a heading line, a stat strip).
export function SkeletonBlock({ lines = 2, label = "Loading" }) {
  return (
    <div className="skeleton" role="status">
      <span className="sr-only">{label}…</span>
      <div aria-hidden="true">
        {Array.from({ length: lines }, (_, i) => (
          <span key={i} className={`sk ${i === 0 ? "sk-title" : "sk-sub"}`} />
        ))}
      </div>
    </div>
  );
}

const PHONE = "(max-width: 640px)";

// True on phone-width screens (the same breakpoint as the bottom tab bar).
export function useIsPhone() {
  const [phone, setPhone] = useState(() => window.matchMedia(PHONE).matches);
  useEffect(() => {
    const mq = window.matchMedia(PHONE);
    const on = () => setPhone(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return phone;
}

export const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
