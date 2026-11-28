// The account button in the header (sidebar foot on wide screens) and its small menu: who is signed in,
// with which role, and Sign out. Signing out deletes register marks kept on this device, so when there
// are any it asks first instead of losing them silently.
import { useEffect, useId, useRef, useState } from "react";
import { plural } from "./format.js";

const ROLE_NAMES = { secretary: "Secretary", assistant: "Assistant", viewer: "Viewer" };

export default function AccountMenu({ user, countDrafts, onSignOut }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [drafts, setDrafts] = useState(0); // > 0 while asking to confirm
  const [busy, setBusy] = useState(false);
  const wrapRef = useRef(null);
  const triggerRef = useRef(null);
  const firstRef = useRef(null);

  const close = (refocus) => {
    setOpen(false);
    setDrafts(0);
    if (refocus) triggerRef.current?.focus();
  };

  useEffect(() => {
    if (!open) return undefined;
    firstRef.current?.focus();
    const onDown = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) close(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close(true);
      }
    };
    const onHash = () => close(false);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("hashchange", onHash);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("hashchange", onHash);
    };
  }, [open]);

  // Focus follows the step: the warning's Cancel button when it appears.
  useEffect(() => {
    if (open) firstRef.current?.focus();
  }, [drafts, open]);

  const signOut = async () => {
    setBusy(true);
    await onSignOut();
  };

  const askOrSignOut = () => {
    const n = countDrafts();
    if (n > 0) setDrafts(n);
    else signOut();
  };

  const role = ROLE_NAMES[user.role] ?? user.role;
  const initial = (user.username || "?").trim().charAt(0).toUpperCase();

  return (
    <div className="account" ref={wrapRef}>
      <button
        type="button"
        ref={triggerRef}
        className="account-trigger"
        aria-expanded={open}
        aria-controls={`${id}-panel`}
        onClick={() => (open ? close(false) : setOpen(true))}
      >
        <span className="avatar" aria-hidden="true">
          {initial}
        </span>
        <span className="account-name">
          {user.username}
          <span className="account-role">{role}</span>
        </span>
        <span className="sr-only">, account</span>
      </button>
      {open && (
        <div className="account-panel" id={`${id}-panel`} role="group" aria-label="Account">
          <p className="account-who">
            Signed in as <strong>{user.username}</strong>
            <span className="account-who-role muted">{role}</span>
          </p>
          {drafts > 0 ? (
            <div role="alert" className="account-warn">
              <p>
                {plural(drafts, "register has", "registers have")} unsaved marks on this device. Signing out deletes them. Save the register first to keep
                them.
              </p>
              <div className="account-actions">
                <button type="button" ref={firstRef} onClick={() => close(true)} disabled={busy}>
                  Cancel
                </button>
                <button type="button" className="danger" onClick={signOut} disabled={busy}>
                  {busy ? "Signing out…" : "Sign out and delete marks"}
                </button>
              </div>
            </div>
          ) : (
            <button type="button" ref={firstRef} className="account-signout" onClick={askOrSignOut} disabled={busy}>
              {busy ? "Signing out…" : "Sign out"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
