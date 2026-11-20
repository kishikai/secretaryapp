import { useCallback, useEffect, useMemo, useState } from "react";
import { api, onUnauthorized } from "./api.js";
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
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return useMemo(() => readTab(tabs), [hash, tabs]);
}

function Shell({ user, onSignOut }) {
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

  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="app-header">
        <div className="app-header-inner">
          <span className="brand">Secretary</span>
          <nav aria-label="Main">
            <ul className="tabs">
              {tabs.map((t) => (
                <li key={t.id}>
                  <a href={`#${t.id}`} aria-current={tab === t.id ? "page" : undefined}>
                    {t.label}
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
