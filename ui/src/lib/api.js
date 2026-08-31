// PLT-04 — shared API helpers.
//
// Centralizes what every page used to do inline: build the API_BASE URL
// and call fetch() directly. Now also attaches the bearer token SEC-01/
// SEC-02 require — none of the original CDN-Babel pages ever sent one,
// because the backend didn't require auth when they were written.
// Routing every API call through apiFetch (instead of a global
// window.fetch monkey-patch) keeps the auth logic in one place without
// silently intercepting fetches this app doesn't own (browser extensions,
// future third-party embeds, etc).

export const API_BASE = "/api/v1";

const TOKEN_KEY = "vaidyaai_access_token";

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage unavailable (private mode, etc). Login still works for the
    // current page load; it just won't survive a refresh.
  }
}

/** fetch() against API_BASE with the bearer token attached, when present. */
export async function apiFetch(path, options = {}) {
  const token = getToken();
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(`${API_BASE}${path}`, { ...options, headers });
}

/** Build a same-origin ws:// or wss:// URL with the token as a query
 * param — see app/routes/websocket_routes.py, which reads it from there
 * rather than a header (a browser WebSocket can't set one on connect). */
export function wsUrl(path) {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  const token = getToken();
  const qs = token ? `?token=${encodeURIComponent(token)}` : "";
  return `${protocol}://${window.location.host}${path}${qs}`;
}
