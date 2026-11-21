// Single fetch wrapper for the backend (same origin, no /api prefix).
//
// Besides plain requests it keeps a small in-memory cache of GET answers so a page can show the
// last data at once and refresh behind it (swr), lets a link start loading its page before the
// tap finishes (prefetch), retries requests that are safe to repeat when the connection drops,
// and sends an Idempotency-Key with create requests so a retry can't make a duplicate.

const GENERIC_ERROR = "Check the form and try again";

export class ApiError extends Error {
  constructor(status, detail, retryAfter = null) {
    let message = GENERIC_ERROR;
    if (typeof detail === "string" && detail) message = detail;
    else if (status === 0) message = "Could not reach the server. Check your connection and try again.";
    else if (status >= 500 && !Array.isArray(detail)) message = "Something went wrong on the server";
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.retryAfter = retryAfter;
  }
}

let unauthorizedHandler = null;

// Register a callback for "session ended" (401 on any non-auth call).
// Returns a function that unregisters it.
export function onUnauthorized(fn) {
  unauthorizedHandler = fn;
  return () => {
    if (unauthorizedHandler === fn) unauthorizedHandler = null;
  };
}

// ----- Cache -----

const MAX_CACHED = 60;
const cache = new Map(); // path -> { data, at }
const inflight = new Map(); // path -> Promise of a GET that is running

function remember(path, data) {
  cache.delete(path);
  cache.set(path, { data, at: Date.now() });
  if (cache.size > MAX_CACHED) cache.delete(cache.keys().next().value);
}

// Forget every cached answer: after any change, on sign-out and when the session ends.
export function clearCache() {
  cache.clear();
  inflight.clear();
}

// ----- Idempotency keys -----

// crypto.randomUUID needs a secure context; plain-HTTP deployments (e.g. a Tailscale IP) fall back.
export function newIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) {
    try {
      return crypto.randomUUID();
    } catch {
      // not a secure context
    }
  }
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

// ----- Requests -----

const RETRY_DELAYS = [400, 1200]; // ms; two retries after the first try
const RETRY_STATUSES = new Set([0, 502, 503, 504]);
// Retrying a POST is safe because the server replays a repeated Idempotency-Key instead of acting twice.
const RETRY_POSTS = true;
// The server's answers about the key itself (see backend/app/idempotency.py).
const KEY_BUSY = "still in progress"; // 409: the first try is still running; wait and ask again
const KEY_REUSED = "already used for a different request"; // 422: the form changed since; use a new key
const detailHas = (e, text) => typeof e.detail === "string" && e.detail.includes(text);

const wait = (ms, signal) =>
  new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });

async function once(path, init) {
  let res;
  try {
    res = await fetch(path, init);
  } catch (e) {
    if (e?.name === "AbortError") throw e;
    throw new ApiError(0, null);
  }
  if (!res.ok) {
    let detail = null;
    try {
      detail = (await res.json()).detail ?? null;
    } catch {
      // body was not JSON
    }
    throw new ApiError(res.status, detail, res.headers.get("Retry-After"));
  }
  if (res.status === 204) return null;
  return res.json();
}

// options: method, body (JSON), form (FormData), signal, idempotencyKey (POST only; one is made if omitted).
export async function api(path, { method = "GET", body, form, signal, idempotencyKey } = {}) {
  const headers = {};
  const init = { method, credentials: "same-origin", headers, signal };
  const isAuth = path.startsWith("/auth/");
  if (method !== "GET") headers["X-Requested-With"] = "secretary";
  const keyed = method === "POST" && !isAuth;
  if (keyed) headers["Idempotency-Key"] = idempotencyKey || newIdempotencyKey();
  if (form) {
    init.body = form; // the browser sets the multipart boundary
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  // GET, PUT, PATCH and DELETE are safe to repeat; a POST is because it carries an Idempotency-Key,
  // once the server honours it (RETRY_POSTS).
  const canRetry = !isAuth && (method !== "POST" || RETRY_POSTS);
  let attempt = 0;
  let rekeyed = false;
  for (;;) {
    try {
      const data = await once(path, init);
      if (method === "GET") remember(path, data);
      else clearCache(); // something changed: cached lists may be out of date
      return data;
    } catch (e) {
      if (e instanceof ApiError) {
        // A DELETE whose first try went through but whose answer was lost comes back as 404 on the retry.
        if (method === "DELETE" && attempt > 0 && e.status === 404) {
          clearCache();
          return null;
        }
        // The same key was used for a request with different content (someone edited the form after
        // a definite answer). This content never ran under that key, so send it once with a new one.
        if (keyed && !rekeyed && e.status === 422 && detailHas(e, KEY_REUSED)) {
          headers["Idempotency-Key"] = newIdempotencyKey();
          rekeyed = true;
          continue;
        }
        const busy = keyed && e.status === 409 && detailHas(e, KEY_BUSY);
        if (canRetry && (RETRY_STATUSES.has(e.status) || busy) && attempt < RETRY_DELAYS.length) {
          await wait(RETRY_DELAYS[attempt], signal);
          attempt += 1;
          continue;
        }
        if (e.status === 401 && !isAuth) {
          clearCache();
          unauthorizedHandler?.();
        }
      }
      throw e;
    }
  }
}

// One shared GET per path at a time, so a prefetch and the page's own load don't both hit the server.
// The request keeps running if the caller aborts, so its answer still fills the cache.
function sharedGet(path, signal) {
  let p = inflight.get(path);
  if (!p) {
    p = api(path).finally(() => inflight.delete(path));
    inflight.set(path, p);
  }
  if (!signal) return p;
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException("Aborted", "AbortError"));
    signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    p.then(resolve, reject);
  });
}

// Stale-while-revalidate: calls onData(cached, {stale: true}) at once when there is a cached answer,
// then onData(fresh, {stale: false}) when the server answers. Returns a promise of the fresh data, which
// rejects on failure (the caller keeps showing the cached data and reports the error).
export function swr(path, onData, { signal } = {}) {
  const hit = cache.get(path);
  if (hit) onData(hit.data, { stale: true });
  return sharedGet(path, signal).then((data) => {
    if (!signal?.aborted) onData(data, { stale: false });
    return data;
  });
}

// Whether a cached answer exists (lets a page skip its skeleton).
export const isCached = (path) => cache.has(path);

// Start loading a page's data early (on press or hover). Skipped when a recent answer is cached.
const FRESH_MS = 15000;
export function prefetch(path) {
  const hit = cache.get(path);
  if (hit && Date.now() - hit.at < FRESH_MS) return;
  sharedGet(path).catch(() => {});
}

// Build "?a=1&b=2" from an object, skipping empty values.
export function query(params) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const isAbort = (e) => e?.name === "AbortError";
