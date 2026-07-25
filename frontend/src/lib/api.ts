// Thin fetch wrapper: attaches the bearer token, refreshes once on 401, and
// surfaces the backend's `detail` message as an ApiError.

const ACCESS_KEY = "dbui.access";
const REFRESH_KEY = "dbui.refresh";

export function getAccessToken(): string | null {
  return localStorage.getItem(ACCESS_KEY);
}

function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

function setTokens(access: string, refresh: string): void {
  localStorage.setItem(ACCESS_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens(): void {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
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
  setTokens(data.access_token, data.refresh_token);
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

export async function login(username: string, password: string): Promise<void> {
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
  setTokens(data.access_token, data.refresh_token);
}
