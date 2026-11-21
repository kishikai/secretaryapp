// A modal panel: a bottom sheet on phones (drag the handle or header down to close), a centred
// dialog on larger screens. Built on <dialog>, so focus stays inside, Escape closes it and the
// page behind is inert. Motion uses transitions (not keyframes), so a drag can take over an
// opening or closing slide at any point, and it is skipped for people who turn motion off.
import { useEffect, useId, useRef } from "react";
import { prefersReducedMotion, useIsPhone } from "./ui.jsx";

const CLOSE_DISTANCE = 80; // px dragged down
const CLOSE_VELOCITY = 0.5; // px per ms at release

export default function Sheet({ open, onClose, title, children, footer = null, className = "" }) {
  const ref = useRef(null);
  const panelRef = useRef(null);
  const opener = useRef(null);
  const drag = useRef(null);
  const closing = useRef(false);
  const titleId = useId();
  const phone = useIsPhone();

  // Open and close the native dialog to match `open`.
  useEffect(() => {
    const dlg = ref.current;
    if (!dlg) return;
    if (open && !dlg.open) {
      opener.current = document.activeElement;
      closing.current = false;
      dlg.showModal();
      const panel = panelRef.current;
      if (phone && panel && !prefersReducedMotion()) {
        panel.style.transition = "none";
        panel.style.transform = "translateY(100%)";
        requestAnimationFrame(() => {
          panel.style.transition = "";
          panel.style.transform = "";
        });
      }
    } else if (!open && dlg.open) {
      finishClose();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function finishClose() {
    const dlg = ref.current;
    if (dlg?.open) dlg.close();
    const back = opener.current;
    if (back && back.isConnected && typeof back.focus === "function") back.focus();
  }

  // Slide away, then tell the parent. The parent's open=false then closes the dialog.
  function requestClose() {
    if (closing.current) return;
    closing.current = true;
    const panel = panelRef.current;
    if (phone && panel && !prefersReducedMotion()) {
      panel.style.transition = "";
      panel.style.transform = "translateY(100%)";
      let done = false;
      const end = () => {
        if (done) return;
        done = true;
        onClose();
      };
      panel.addEventListener("transitionend", end, { once: true });
      setTimeout(end, 260); // in case transitionend doesn't fire
    } else {
      onClose();
    }
  }

  const onCancel = (e) => {
    e.preventDefault(); // Escape: animate like any other close
    requestClose();
  };

  // A click on the backdrop (the dialog itself, outside the panel) closes it.
  const onClick = (e) => {
    if (e.target === ref.current) requestClose();
  };

  const onPointerDown = (e) => {
    if (!phone || e.button > 0) return;
    if (e.target.closest("button, a, input, select, textarea")) return;
    const panel = panelRef.current;
    closing.current = false;
    drag.current = { y0: e.clientY, t0: performance.now(), last: e.clientY, lastT: performance.now(), dy: 0 };
    panel.style.transition = "none";
    e.currentTarget.setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e) => {
    const d = drag.current;
    if (!d) return;
    d.dy = Math.max(0, e.clientY - d.y0);
    d.v = (e.clientY - d.last) / Math.max(1, performance.now() - d.lastT);
    d.last = e.clientY;
    d.lastT = performance.now();
    panelRef.current.style.transform = `translateY(${d.dy}px)`;
  };
  const onPointerUp = () => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    const panel = panelRef.current;
    panel.style.transition = "";
    if (d.dy > CLOSE_DISTANCE || (d.v ?? 0) > CLOSE_VELOCITY) requestClose();
    else panel.style.transform = "";
  };

  return (
    <dialog ref={ref} className={`sheet ${className}`} aria-labelledby={titleId} onCancel={onCancel} onClick={onClick} onClose={() => open && onClose()}>
      <div className="sheet-panel" ref={panelRef}>
        <div className="sheet-head" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
          <span className="sheet-grab" aria-hidden="true" />
          <h2 id={titleId}>{title}</h2>
          <button type="button" className="sheet-close" onClick={requestClose}>
            Close
          </button>
        </div>
        <div className="sheet-body">{open ? children : null}</div>
        {footer && open && <div className="sheet-foot">{footer}</div>}
      </div>
    </dialog>
  );
}
