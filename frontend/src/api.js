// Single fetch wrapper for the backend (same origin, no /api prefix).

const GENERIC_ERROR = "Check the form and try again";

export class ApiError extends Error {
  constructor(status, detail, retryAfter = null) {
    let message = GENERIC_ERROR;
    if (typeof detail === "string" && detail) message = detail;
    else if (status === 0) message = "Could not reach the server";
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

export async function api(path, { method = "GET", body, form, signal } = {}) {
  const headers = {};
  const init = { method, credentials: "same-origin", headers, signal };
  if (method !== "GET") headers["X-Requested-With"] = "secretary";
  if (form) {
    init.body = form; // the browser sets the multipart boundary
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

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
    if (res.status === 401 && !path.startsWith("/auth/") && unauthorizedHandler) unauthorizedHandler();
    throw new ApiError(res.status, detail, res.headers.get("Retry-After"));
  }
  if (res.status === 204) return null;
  return res.json();
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
