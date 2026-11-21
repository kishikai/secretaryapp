import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { api, clearCache, onUnauthorized, prefetch } from "./api.js";
import { dataPathFor } from "./prefetch.js";
import { Toaster } from "./toast.jsx";
import { prefersReducedMotion } from "./ui.jsx";
import GlobalSearch from "./Search.jsx";
import Login from "./pages/Login.jsx";
import Overview from "./pages/Overview.jsx";
import Messages from "./pages/Messages.jsx";
import Notifications from "./pages/Notifications.jsx";
import Rules from "./pages/Rules.jsx";
import Members from "./pages/Members.jsx";
import Groups from "./pages/Groups.jsx";
import Events from "./pages/Events.jsx";
import Absences from "./pages/Absences.jsx";
import Evidence from "./pages/Evidence.jsx";
import Announcements from "./pages/Announcements.jsx";
import { HIDDEN_TABS, MESSAGES_ENABLED } from "./features.js";

// Simple 24px line icons for the phone tab bar (decorative: every tab also has its text label).
const ICONS = {
  overview: <path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" />,
  members: (
    <>
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c0-4 3.6-6 8-6s8 2 8 6" />
    </>
  ),
  groups: (
    <>
      <circle cx="9" cy="8" r="3.5" />
      <path d="M2 20c0-3.5 3-5.5 7-5.5s7 2 7 5.5" />
      <circle cx="17" cy="9" r="2.75" />
      <path d="M17.5 14.6c2.6.3 4.5 2 4.5 5.4" />
    </>
  ),
  events: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4M8 15l2.5 2.5L16 13" />
    </>
  ),
  absences: <path d="M4 21V11M10 21V5M16 21v-6M21 21H3" />,
};
const TabIcon = ({ id }) => (
  <svg className="tab-icon" viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" focusable="false">
    {ICONS[id] ?? <circle cx="12" cy="12" r="3" />}
  </svg>
);
// A bottom tab bar on phones holds at most five tabs; with more, the phone keeps the scrolling top strip.
const MAX_BOTTOM_TABS = 5;

// secretaryOnly tabs call endpoints that answer 403 to other roles; editorOnly tabs are hidden from viewers.

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "members", label: "Members" },
  { id: "groups", label: "Groups" },
  { id: "events", label: "Events" },
  { id: "announcements", label: "Announcements", editorOnly: true },
  { id: "absences", label: "Absences" },
  { id: "evidence", label: "Review", secretaryOnly: true },
  { id: "messages", label: "Messages", secretaryOnly: true },
  { id: "notifications", label: "Notifications", secretaryOnly: true },
  { id: "rules", label: "Rules", secretaryOnly: true },
];

// "#members?group=3" -> { id: "members", params: {group: "3"} }. Unknown or not-allowed tabs fall back to the first one.
function readTab(tabs) {
  const [id, search = ""] = window.location.hash.slice(1).split("?");
  const found = tabs.find((t) => t.id === id) ?? tabs[0];
  return { id: found.id, params: new URLSearchParams(search) };
}

function useHashTab(tabs) {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    // A page change cross-fades where the browser supports view transitions (not with reduced motion).
    const onChange = () => {
      const update = () => flushSync(() => setHash(window.location.hash));
      if (document.startViewTransition && !prefersReducedMotion() && !document.hidden) document.startViewTransition(update);
      else update();
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return useMemo(() => readTab(tabs), [hash, tabs]);
}

// Start loading a link's page as soon as it is pressed (or hovered for a moment with a mouse),
// so the data is usually there by the time the tap or click finishes.
function usePrefetchOnPress() {
  useEffect(() => {
    let hoverTimer = null;
    const linkPath = (e) => {
      const a = e.target instanceof Element ? e.target.closest('a[href^="#"]') : null;
      return a ? dataPathFor(a.getAttribute("href")) : null;
    };
    const onDown = (e) => {
      const p = linkPath(e);
      if (p) prefetch(p);
    };
    const onOver = (e) => {
      if (e.pointerType !== "mouse") return;
      clearTimeout(hoverTimer);
      const p = linkPath(e);
      if (p) hoverTimer = setTimeout(() => prefetch(p), 120);
    };
    const onOut = () => clearTimeout(hoverTimer);
    document.addEventListener("pointerdown", onDown, { passive: true });
    document.addEventListener("pointerover", onOver, { passive: true });
    document.addEventListener("pointerout", onOut, { passive: true });
    return () => {
      clearTimeout(hoverTimer);
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("pointerover", onOver);
      document.removeEventListener("pointerout", onOut);
    };
  }, []);
}

// Unsaved register marks kept on this device (see Register.jsx). Removed when someone signs out,
// so the next person on a shared device doesn't see them.
const DRAFT_PREFIX = "secretary.registerDraft.";
function clearDrafts() {
  try {
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i);
      if (k?.startsWith(DRAFT_PREFIX)) localStorage.removeItem(k);
    }
  } catch {
    // storage blocked: nothing was kept
  }
}

function Shell({ user, onSignOut }) {
  usePrefetchOnPress();
  const isSecretary = user.role === "secretary";
  const isViewer = user.role === "viewer";
  const tabs = useMemo(() => TABS.filter((t) => !HIDDEN_TABS.includes(t.id) && (isSecretary || !t.secretaryOnly) && (!isViewer || !t.editorOnly)), [isSecretary, isViewer]);
  const { id: tab, params } = useHashTab(tabs);
  const [unread, setUnread] = useState(0);
  const [collectorOffline, setCollectorOffline] = useState(false);

  const refreshUnread = useCallback(async () => {
    try {
      const s = await api("/stats");
      setUnread(s.unread_notifications ?? 0);
    } catch {
      // keep the previous count; a 401 is handled globally
    }
  }, []);

  useEffect(() => {
    // /stats is secretary-only, and the count is only shown on the Notifications tab.
    if (!isSecretary || !MESSAGES_ENABLED || HIDDEN_TABS.includes("notifications")) return undefined;
    refreshUnread();
    const id = setInterval(() => {
      if (!document.hidden) refreshUnread();
    }, 30000);
    return () => clearInterval(id);
  }, [refreshUnread, isSecretary]);

  // Offline only when at least one collector exists and none has checked in recently.
  useEffect(() => {
    if (!isSecretary || !MESSAGES_ENABLED) return undefined; // /collector/status is secretary-only
    let cancelled = false;
    const check = async () => {
      try {
        const c = await api("/collector/status");
        if (!cancelled) setCollectorOffline((c.collectors ?? []).length > 0 && !(c.collectors ?? []).some((x) => x.online));
      } catch {
        // keep the previous state
      }
    };
    check();
    const id = setInterval(() => {
      if (!document.hidden) check();
    }, 60000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [isSecretary]);

  useEffect(() => {
    const label = tabs.find((t) => t.id === tab).label;
    document.title = `${label} · Secretary`;
  }, [tab, tabs]);

  // Moving between tabs puts focus on the new page's heading, so screen readers announce it.
  // Detail views (an event, a member) focus their own heading once loaded.
  const firstTab = useRef(true);
  useEffect(() => {
    if (firstTab.current) {
      firstTab.current = false;
      return;
    }
    const h = document.querySelector("#main h1");
    if (h) {
      if (!h.hasAttribute("tabindex")) h.setAttribute("tabindex", "-1");
      h.focus({ preventScroll: false });
    }
  }, [tab]);

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="app-header">
        <div className="app-header-inner">
          <span className="brand">Secretary</span>
          <nav aria-label="Main" className={tabs.length <= MAX_BOTTOM_TABS ? "has-bottom-tabs" : undefined}>
            <ul className="tabs">
              {tabs.map((t) => (
                <li key={t.id}>
                  <a href={`#${t.id}`} aria-current={tab === t.id ? "page" : undefined}>
                    <TabIcon id={t.id} />
                    <span className="tab-label">{t.label}</span>
                    {t.id === "notifications" && unread > 0 && (
                      <span className="badge">
                        {unread}
                        <span className="sr-only"> unread</span>
                      </span>
                    )}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
          <GlobalSearch />
          <div className="user-box">
            <span className="muted">{user.username}</span>
            <button type="button" onClick={onSignOut}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      {isSecretary && MESSAGES_ENABLED && collectorOffline && (
        <div className="collector-notice" role="status">
          <p>
            The message collector is offline. Attendance still works — you can take registers by hand. <a href="#overview">See details</a>
          </p>
        </div>
      )}
      <Toaster />
      <main id="main" className="page">
        {tab === "overview" && <Overview role={user.role} />}
        {tab === "members" && <Members role={user.role} groupParam={params.get("group")} idParam={params.get("id")} />}
        {tab === "groups" && <Groups role={user.role} />}
        {tab === "events" && <Events role={user.role} idParam={params.get("id")} />}
        {tab === "announcements" && <Announcements />}
        {tab === "absences" && <Absences />}
        {tab === "evidence" && <Evidence />}
        {tab === "messages" && <Messages />}
        {tab === "notifications" && <Notifications onUnreadChange={refreshUnread} />}
        {tab === "rules" && <Rules />}
      </main>
    </>
  );
}

export default function App() {
  // undefined = checking the session, null = signed out
  const [user, setUser] = useState(undefined);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let cancelled = false;
    api("/auth/me")
      .then((me) => !cancelled && setUser(me))
      .catch(() => !cancelled && setUser(null));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(
    () =>
      onUnauthorized(() => {
        clearCache();
        setNotice("Your session ended. Sign in again.");
        setUser(null);
      }),
    [],
  );

  const signOut = async () => {
    try {
      await api("/auth/logout", { method: "POST" });
    } catch {
      // leave the screen either way
    }
    clearCache();
    clearDrafts();
    setNotice("");
    setUser(null);
  };

  if (user === undefined) {
    return (
      <p className="boot" role="status">
        Loading…
      </p>
    );
  }
  if (user === null) {
    return (
      <Login
        notice={notice}
        onLogin={(me) => {
          setNotice("");
          setUser(me);
        }}
      />
    );
  }
  return <Shell user={user} onSignOut={signOut} />;
}
