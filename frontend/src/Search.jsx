// Global search: one button in the header (and Ctrl/Cmd+K, or "/") opens a dialog that finds a member
// or an event by name and opens it. The dialog is an ARIA combobox: focus stays in the input while
// the arrow keys move a highlighted option through the results.
import { Fragment, useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { api, isAbort, prefetch, query } from "./api.js";
import { errorText, formatEventWhen, plural, todayLocal } from "./format.js";
import { dataPathFor } from "./prefetch.js";
import Sheet from "./Sheet.jsx";
import { SkeletonRows, useIsPhone } from "./ui.jsx";
import { StatusChip } from "./pages/eventUi.jsx";

const DEBOUNCE_MS = 200;
const PER_GROUP = 6;

const isApple = () => /Mac|iPhone|iPad|iPod/i.test(navigator.userAgentData?.platform || navigator.platform || "");

// True when typing "/" should be left to the focused element.
function inTextField(el) {
  if (!(el instanceof Element)) return false;
  if (el.isContentEditable) return true;
  const tag = el.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag !== "INPUT") return false;
  return !["button", "checkbox", "radio", "submit", "reset", "range", "color", "file", "image"].includes(el.type);
}

function Magnifier() {
  return (
    <svg className="search-icon" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m15.5 15.5 5 5" />
    </svg>
  );
}

// The name with the searched words in <mark>. Built from text nodes only, so nothing is parsed as HTML.
function Highlight({ text, words }) {
  if (!text || words.length === 0) return text ?? "";
  const re = new RegExp(`(${words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  // With one capture group, split() puts the matched pieces at the odd positions.
  return text.split(re).map((part, i) => (i % 2 === 1 ? <mark key={i}>{part}</mark> : <Fragment key={i}>{part}</Fragment>));
}

function wordsOf(q) {
  return [...new Set(q.split(/\s+/).filter(Boolean))].sort((a, b) => b.length - a.length);
}

// Upcoming first (soonest first), then past (most recent first); events without a date go last.
function orderEvents(events) {
  const today = todayLocal();
  const day = (e) => (e.starts_at ? String(e.starts_at).slice(0, 10) : "");
  const key = (e) => String(e.starts_at ?? "");
  const upcoming = events.filter((e) => day(e) && day(e) >= today).sort((a, b) => key(a).localeCompare(key(b)));
  const past = events.filter((e) => day(e) && day(e) < today).sort((a, b) => key(b).localeCompare(key(a)));
  return [...upcoming, ...past, ...events.filter((e) => !day(e))];
}

function SearchPanel({ onOpenResult, onClose }) {
  const uid = useId();
  const listId = `${uid}-list`;
  const [text, setText] = useState("");
  const [results, setResults] = useState(null); // { q, members, events, errors }
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(-1);
  const inputRef = useRef(null);
  const listRef = useRef(null);
  const ctrl = useRef(null);
  const timer = useRef(null);
  const runningFor = useRef(""); // the term the request in flight is for
  const goWhenReady = useRef(false); // Enter was pressed before the answer arrived
  const q = text.trim();

  // The native dialog opens after this renders, so ask it to focus the input (autofocus attribute)
  // and also focus it ourselves once the dialog is showing.
  useEffect(() => {
    const el = inputRef.current;
    el?.setAttribute("autofocus", "");
    const id = requestAnimationFrame(() => {
      if (document.activeElement !== el) el?.focus();
    });
    return () => cancelAnimationFrame(id);
  }, []);

  // On phones the on-screen keyboard covers the bottom of the sheet: lift it by the keyboard's height.
  useEffect(() => {
    const vv = window.visualViewport;
    const dlg = inputRef.current?.closest("dialog");
    if (!vv || !dlg) return undefined;
    const fit = () => {
      const kb = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
      dlg.style.setProperty("--search-kb", `${Math.round(kb)}px`);
      dlg.style.setProperty("--search-vh", `${Math.round(vv.height)}px`);
    };
    fit();
    vv.addEventListener("resize", fit);
    vv.addEventListener("scroll", fit);
    return () => {
      vv.removeEventListener("resize", fit);
      vv.removeEventListener("scroll", fit);
      dlg.style.removeProperty("--search-kb");
      dlg.style.removeProperty("--search-vh");
    };
  }, []);

  const run = useCallback(async (term) => {
    ctrl.current?.abort();
    const c = new AbortController();
    ctrl.current = c;
    runningFor.current = term;
    setLoading(true);
    const get = (path) => api(`${path}${query({ q: term })}`, { signal: c.signal });
    const [m, e] = await Promise.allSettled([get("/members"), get("/events")]);
    if (c.signal.aborted || [m, e].some((r) => r.status === "rejected" && isAbort(r.reason))) return;
    const errors = [];
    // A 403 means this role may not see that group: leave it out without an error.
    const take = (r, label) => {
      if (r.status === "fulfilled") return r.value;
      if (r.reason?.status !== 403) errors.push(`Could not search ${label}: ${errorText(r.reason)}`);
      return [];
    };
    const members = take(m, "members").slice(0, PER_GROUP);
    const events = orderEvents(take(e, "events")).slice(0, PER_GROUP);
    setResults({ q: term, members, events, errors });
    setActive(-1);
    setLoading(false);
  }, []);

  // Search 200ms after the last keystroke.
  useEffect(() => {
    if (!q) {
      ctrl.current?.abort();
      setResults(null);
      setLoading(false);
      setActive(-1);
      return undefined;
    }
    timer.current = setTimeout(() => run(q), DEBOUNCE_MS);
    return () => {
      clearTimeout(timer.current);
      ctrl.current?.abort();
    };
  }, [q, run]);

  const fresh = results && results.q === q ? results : null;
  const shown = results; // keep the previous answer on screen while the next one loads
  const options = useMemo(() => {
    if (!shown) return [];
    return [
      ...shown.members.map((m) => ({ id: `${uid}-m${m.id}`, href: `#members?id=${m.id}`, kind: "member", item: m })),
      ...shown.events.map((e) => ({ id: `${uid}-e${e.id}`, href: `#events?id=${e.id}`, kind: "event", item: e })),
    ];
  }, [shown, uid]);

  // The page behind starts loading as soon as an option is highlighted.
  const activeOption = options[active];
  useEffect(() => {
    if (activeOption) prefetch(dataPathFor(activeOption.href));
  }, [activeOption]);
  useEffect(() => {
    if (activeOption) document.getElementById(activeOption.id)?.scrollIntoView({ block: "nearest" });
  }, [activeOption]);

  // Enter before the answer arrived: open the first result as soon as it does.
  useEffect(() => {
    if (goWhenReady.current && fresh) {
      goWhenReady.current = false;
      if (options.length) onOpenResult(options[0].href);
    }
  }, [fresh, options, onOpenResult]);

  const onKeyDown = (e) => {
    if (e.nativeEvent.isComposing) return;
    const n = options.length;
    if (e.key === "Escape") {
      e.preventDefault(); // a search box would only clear its text first; close the dialog at once
      onClose();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!n) return;
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((a) => (a < 0 ? (step === 1 ? 0 : n - 1) : (a + step + n) % n));
    } else if (e.key === "Home" && n) {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End" && n) {
      e.preventDefault();
      setActive(n - 1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (!q) return;
      if (!fresh) {
        // Still typing or loading: search now and open the first result when it comes.
        goWhenReady.current = true;
        clearTimeout(timer.current);
        if (!loading || runningFor.current !== q) run(q);
        return;
      }
      const o = options[active] ?? options[0];
      if (o) onOpenResult(o.href);
    }
  };

  const words = wordsOf(q);
  const members = fresh ? fresh.members.length : 0;
  const events = fresh ? fresh.events.length : 0;
  const announce = !fresh ? "" : members + events === 0 ? "No results" : `${plural(members, "member", "members")} and ${plural(events, "event", "events")}`;

  const optionProps = (o, i) => ({
    id: o.id,
    role: "option",
    "aria-selected": i === active,
    className: `search-option${i === active ? " is-active" : ""}`,
    onClick: () => onOpenResult(o.href),
    onPointerDown: () => prefetch(dataPathFor(o.href)),
    onPointerMove: (e) => {
      if (e.pointerType === "mouse" && i !== active) setActive(i);
    },
  });

  const memberCount = shown?.members.length ?? 0;
  const hasOptions = options.length > 0;

  return (
    <>
      <div className="search-field">
        <Magnifier />
        <input
          ref={inputRef}
          type="search"
          className="search-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          role="combobox"
          aria-label="Search members and events"
          aria-expanded={hasOptions}
          aria-controls={listId}
          aria-activedescendant={activeOption?.id}
          aria-autocomplete="list"
          enterKeyHint="search"
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="Name, email, phone, place…"
        />
      </div>
      <div className="sr-only" role="status" aria-live="polite">
        {announce}
      </div>
      <div className="search-results" aria-busy={loading}>
        {!q && <p className="search-hint muted">Find a member by name, email, phone or code, or an event by name, place or category.</p>}
        {q && !shown && <SkeletonRows rows={4} card={false} label="Searching" />}
        {shown?.errors.map((msg) => (
          <p key={msg} className="search-error" role="alert">
            {msg}
          </p>
        ))}
        {q && fresh && !hasOptions && fresh.errors.length === 0 && <p className="search-empty muted">No members or events match "{q}".</p>}
        <div id={listId} role="listbox" aria-label="Results" ref={listRef} hidden={!q || !hasOptions} className={q && !fresh ? "is-stale" : undefined}>
          {memberCount > 0 && (
            <div role="group" aria-labelledby={`${uid}-gm`} className="search-group">
              <div id={`${uid}-gm`} role="presentation" className="search-group-label">
                Members
              </div>
              {options.slice(0, memberCount).map((o, i) => (
                <div key={o.id} {...optionProps(o, i)}>
                  <span className="search-main">
                    <span className="search-name">
                      <Highlight text={o.item.full_name} words={words} />
                    </span>
                    <span className="search-sub muted">{[o.item.email, o.item.phone, o.item.member_code].filter(Boolean).join(" · ")}</span>
                  </span>
                  {o.item.active === false && <span className="chip">Inactive</span>}
                </div>
              ))}
            </div>
          )}
          {options.length > memberCount && (
            <div role="group" aria-labelledby={`${uid}-ge`} className="search-group">
              <div id={`${uid}-ge`} role="presentation" className="search-group-label">
                Events
              </div>
              {options.slice(memberCount).map((o, j) => (
                <div key={o.id} {...optionProps(o, memberCount + j)}>
                  <span className="search-main">
                    <span className="search-name">
                      <Highlight text={o.item.name} words={words} />
                    </span>
                    <span className="search-sub muted">{[formatEventWhen(o.item.starts_at, o.item.ends_at), o.item.location].filter(Boolean).join(" · ")}</span>
                  </span>
                  <StatusChip status={o.item.status} />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

export default function GlobalSearch() {
  const [open, setOpen] = useState(false);
  const phone = useIsPhone();
  const apple = useMemo(isApple, []);

  useEffect(() => {
    const onKey = (e) => {
      if (e.isComposing || e.defaultPrevented) return;
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(true);
      } else if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey && !inTextField(e.target) && !document.querySelector("dialog[open]")) {
        e.preventDefault(); // don't type the slash into the search box
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Close first (the dialog hands focus back to the button), then change the page on the next frame so
  // the new page's heading, which takes focus when it loads, is the last thing to be focused.
  const openResult = useCallback((href) => {
    prefetch(dataPathFor(href));
    setOpen(false);
    requestAnimationFrame(() => requestAnimationFrame(() => (window.location.hash = href.slice(1))));
  }, []);

  return (
    <>
      <button
        type="button"
        className="search-trigger"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-keyshortcuts="Control+K Meta+K"
        aria-label={phone ? "Search" : undefined}
      >
        <Magnifier />
        {!phone && (
          <>
            <span className="search-trigger-text">Search</span>
            <kbd aria-hidden="true">{apple ? "⌘K" : "Ctrl K"}</kbd>
          </>
        )}
      </button>
      <Sheet open={open} onClose={() => setOpen(false)} title="Search" className="search-sheet">
        <SearchPanel onOpenResult={openResult} onClose={() => setOpen(false)} />
      </Sheet>
    </>
  );
}
