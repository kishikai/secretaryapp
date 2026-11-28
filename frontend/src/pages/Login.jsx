import { useId, useState } from "react";
import { api, ApiError } from "../api.js";
import { plural } from "../format.js";

function tooManyAttempts(retryAfter) {
  const secs = Number(retryAfter);
  if (!retryAfter || !Number.isFinite(secs)) return "Too many attempts. Try again later.";
  const mins = Math.max(1, Math.ceil(secs / 60));
  return `Too many attempts. Try again in ${plural(mins, "minute", "minutes")}.`;
}

export default function Login({ onLogin, notice }) {
  const id = useId();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const me = await api("/auth/login", { method: "POST", body: { username, password } });
      onLogin(me);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) setError(tooManyAttempts(err.retryAfter));
      else setError(err.message);
      setBusy(false);
    }
  };

  return (
    <main className="login-wrap">
      <form className="card login" onSubmit={submit}>
        <img className="login-mark" src="/icon-192.png" alt="" width="48" height="48" />
        <h1>Sign in to Secretary</h1>
        <p className="muted login-lead">Members, events and attendance registers.</p>
        {notice && !error && (
          <p role="status" className="notice">
            {notice}
          </p>
        )}
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <div className="field">
          <label htmlFor={`${id}-u`}>Username</label>
          <input
            id={`${id}-u`}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            required
          />
        </div>
        <div className="field">
          <label htmlFor={`${id}-p`}>Password</label>
          <input
            id={`${id}-p`}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </div>
        <button type="submit" className="primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
