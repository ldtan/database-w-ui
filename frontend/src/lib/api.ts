// Thin fetch wrapper: attaches the bearer token, refreshes once on 401, and
// surfaces the backend's `detail` message as an ApiError.

const ACCESS_KEY = "dbui.access";
const REFRESH_KEY = "dbui.refresh";

// Tokens live in exactly one of two stores, chosen by the "Remember me" flag at
// login: localStorage persists across browser restarts, sessionStorage is dropped
// when the tab closes. Reads prefer localStorage but fall back to sessionStorage,
// and writes clear the other store so a stale persistent token can never outlive a
// session-only login.
function readToken(key: string): string | null {
  return localStorage.getItem(key) ?? sessionStorage.getItem(key);
}

export function getAccessToken(): string | null {
  return readToken(ACCESS_KEY);
}

function getRefreshToken(): string | null {
  return readToken(REFRESH_KEY);
}

// The store currently holding tokens, so a refresh writes back where login put them.
function activeStorage(): Storage {
  return sessionStorage.getItem(REFRESH_KEY) != null ? sessionStorage : localStorage;
}

function setTokens(access: string, refresh: string, storage: Storage): void {
  const other = storage === localStorage ? sessionStorage : localStorage;
  other.removeItem(ACCESS_KEY);
  other.removeItem(REFRESH_KEY);
  storage.setItem(ACCESS_KEY, access);
  storage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens(): void {
  for (const store of [localStorage, sessionStorage]) {
    store.removeItem(ACCESS_KEY);
    store.removeItem(REFRESH_KEY);
  }
}

// Sign out: tell the server to revoke the refresh token, *then* forget it locally.
//
// Both halves are needed and the order is the reason. Dropping the tokens from
// storage ends the session only for this browser — the refresh token stays valid
// server-side for REFRESH_TOKEN_EXPIRE_DAYS, so anything that recovers it later still
// holds a live session. And the revoke has to be sent while the token is still
// readable, which is why clearing comes second.
//
// Local storage is cleared *synchronously*, before the request goes out, and the
// revoke is then fire-and-forget. Clearing afterwards — in a `.finally`, say — would
// leave the tokens readable for as long as the round trip took, and would clobber the
// tokens of a *new* sign-in that happened in the meantime: sign out, land on the login
// page, sign in as someone else quickly, and the earlier revoke's cleanup would land
// on the fresh pair and bounce them straight back out.
//
// A network error or a token the server has already forgotten cannot fail a sign-out,
// which is the other half of the same ordering: by the time anything can go wrong,
// this browser is already signed out.
export async function logout(): Promise<void> {
  const refresh = getRefreshToken();
  clearTokens();
  if (!refresh) return;
  try {
    await fetch("/api/auth/tokens/revoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
  } catch {
    /* signing out locally is not conditional on reaching the server */
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function extractDetail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (body?.detail) return JSON.stringify(body.detail);
  } catch {
    /* non-JSON body */
  }
  return fallback;
}

// The server rotates refresh tokens: each refresh revokes the token it was given
// and issues a new one, so a token works exactly once. When the access token
// expires, several in-flight requests can 401 at the same moment; if each fired
// its own refresh they would all present the same token, the first would revoke it,
// and the rest would fail and log the user out. Collapse concurrent callers onto a
// single in-flight refresh so the token is spent once and everyone shares the result.
let inFlightRefresh: Promise<boolean> | null = null;

function refreshTokens(): Promise<boolean> {
  if (!inFlightRefresh) {
    inFlightRefresh = performRefresh().finally(() => {
      inFlightRefresh = null;
    });
  }
  return inFlightRefresh;
}

async function performRefresh(): Promise<boolean> {
  const refresh = getRefreshToken();
  if (!refresh) return false;
  const res = await fetch("/api/auth/tokens/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!res.ok) {
    clearTokens();
    return false;
  }
  const data = await res.json();
  setTokens(data.access_token, data.refresh_token, activeStorage());
  return true;
}

// Perform the fetch with auth + one-shot refresh, returning the raw Response so
// callers can read JSON, a blob (downloads), or nothing.
async function requestRaw(
  path: string,
  options: RequestInit = {},
  retry = true,
): Promise<Response> {
  const headers = new Headers(options.headers);
  const token = getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`/api${path}`, { ...options, headers });

  if (res.status === 401 && retry && (await refreshTokens())) {
    return requestRaw(path, options, false);
  }
  if (!res.ok) {
    throw new ApiError(res.status, await extractDetail(res, res.statusText));
  }
  return res;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await requestRaw(path, { ...options, headers });
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface Page<T> {
  items: T[];
  total: number;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  // A listing plus its total match count, which the server returns in a header so
  // the body stays a plain array. Falls back to the page length if the header is
  // missing — a paginator that thinks there is one page beats one that crashes.
  getPage: async <T>(path: string): Promise<Page<T>> => {
    const res = await requestRaw(path);
    const items = (await res.json()) as T[];
    const total = Number(res.headers.get("X-Total-Count"));
    return { items, total: Number.isFinite(total) ? total : items.length };
  },
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "PATCH",
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  del: (path: string) => request<void>(path, { method: "DELETE" }),
  // Download: POST JSON, receive a file body as a Blob.
  postBlob: (path: string, body: unknown) =>
    requestRaw(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => r.blob()),
  // Upload: POST multipart form data (browser sets the Content-Type boundary).
  postForm: <T>(path: string, form: FormData) =>
    requestRaw(path, { method: "POST", body: form }).then((r) => r.json() as Promise<T>),
};

// `remember` picks where the token pair is stored: true (default) keeps you signed
// in across browser restarts, false confines the session to the current tab.
export async function login(
  username: string,
  password: string,
  remember = true,
): Promise<void> {
  const form = new URLSearchParams();
  form.set("username", username);
  form.set("password", password);
  const res = await fetch("/api/auth/tokens/login", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: form,
  });
  if (!res.ok) {
    throw new ApiError(res.status, await extractDetail(res, "Login failed"));
  }
  const data = await res.json();
  setTokens(
    data.access_token,
    data.refresh_token,
    remember ? localStorage : sessionStorage,
  );
}
