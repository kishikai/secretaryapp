import { useCallback, useEffect, useState } from "react";
import { api, onUnauthorized } from "./api.js";
import Login from "./pages/Login.jsx";
import Overview from "./pages/Overview.jsx";
import Messages from "./pages/Messages.jsx";
import Notifications from "./pages/Notifications.jsx";
import Rules from "./pages/Rules.jsx";

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "messages", label: "Messages" },
  { id: "notifications", label: "Notifications" },
  { id: "rules", label: "Rules" },
];

function readTab() {
  const h = window.location.hash.slice(1);
  return TABS.some((t) => t.id === h) ? h : "overview";
}

function useHashTab() {
  const [tab, setTab] = useState(readTab);
  useEffect(() => {
    const onChange = () => setTab(readTab());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return tab;
}

function Shell({ user, onSignOut }) {
  const tab = useHashTab();
  const [unread, setUnread] = useState(0);

  const refreshUnread = useCallback(async () => {
    try {
      const s = await api("/stats");
      setUnread(s.unread_notifications ?? 0);
    } catch {
      // keep the previous count; a 401 is handled globally
    }
  }, []);

  useEffect(() => {
    refreshUnread();
    const id = setInterval(() => {
      if (!document.hidden) refreshUnread();
    }, 30000);
    return () => clearInterval(id);
  }, [refreshUnread]);

  useEffect(() => {
    const label = TABS.find((t) => t.id === tab).label;
    document.title = `${label} · Secretary`;
  }, [tab]);

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
              {TABS.map((t) => (
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
      <main id="main" className="page">
        {tab === "overview" && <Overview />}
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
