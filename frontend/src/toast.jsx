// Small transient messages, mainly "Removed X. Undo".
// toast({ text, action: { label, run }, timeout }) from anywhere; <Toaster /> renders them once in the shell.
// They never take focus; the region is a polite live region, and a toast stays while hovered or focused.
import { useEffect, useRef, useState } from "react";

const listeners = new Set();
let seq = 0;

export function toast({ text, action = null, timeout = 6000 }) {
  const t = { id: ++seq, text, action, timeout };
  listeners.forEach((fn) => fn(t));
  return t.id;
}

function ToastItem({ t, onDone }) {
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState(t.text);
  const paused = useRef(false);
  const left = useRef(t.timeout);

  useEffect(() => {
    let last = Date.now();
    const id = setInterval(() => {
      const now = Date.now();
      if (!paused.current) left.current -= now - last;
      last = now;
      if (left.current <= 0) onDone(t.id);
    }, 200);
    return () => clearInterval(id);
  }, [t.id, onDone]);

  const pause = () => {
    paused.current = true;
  };
  const resume = () => {
    paused.current = false;
  };

  const runAction = async () => {
    setBusy(true);
    try {
      await t.action.run();
      onDone(t.id);
    } catch (e) {
      setText(e?.message ? `Couldn't undo: ${e.message}` : "Couldn't undo");
      setBusy(false);
      left.current = 6000;
    }
  };

  return (
    <li className="toast" onPointerEnter={pause} onPointerLeave={resume} onFocus={pause} onBlur={resume}>
      <span className="toast-text">{text}</span>
      {t.action && (
        <button type="button" className="toast-action" disabled={busy} onClick={runAction}>
          {busy ? "Undoing…" : t.action.label}
        </button>
      )}
      <button type="button" className="toast-close" aria-label="Dismiss" onClick={() => onDone(t.id)}>
        <span aria-hidden="true">×</span>
      </button>
    </li>
  );
}

export function Toaster() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const fn = (t) => setItems((cur) => [...cur.slice(-2), t]); // at most three at once
    listeners.add(fn);
    return () => listeners.delete(fn);
  }, []);
  const done = useRef((id) => setItems((cur) => cur.filter((x) => x.id !== id))).current;
  return (
    <div className="toaster" role="status" aria-live="polite">
      <ul>
        {items.map((t) => (
          <ToastItem key={t.id} t={t} onDone={done} />
        ))}
      </ul>
    </div>
  );
}
