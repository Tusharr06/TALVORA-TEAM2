export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

type ProblemBody = {
  detail?: string;
  title?: string;
  code?: string;
  invalid_params?: { name: string; reason: string }[];
};

let csrfToken: string | null = null;

export function clearCsrf(): void {
  csrfToken = null;
}

async function readProblem(response: Response): Promise<ProblemBody | null> {
  return (await response.json().catch(() => null)) as ProblemBody | null;
}

function problemMessage(body: ProblemBody | null, fallback: string): string {
  if (!body) return fallback;
  const fields = body.invalid_params?.map((item) => `${item.name}: ${item.reason}`).join(" ");
  return [body.detail, fields].filter(Boolean).join(" ") || body.title || fallback;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  const isForm = typeof FormData !== "undefined" && init.body instanceof FormData;
  if (init.body && !isForm && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (method !== "GET" && method !== "HEAD") {
    if (!csrfToken) {
      const token = await api<{ csrf_token: string }>("/api/v1/auth/csrf");
      csrfToken = token.csrf_token;
    }
    headers.set("X-CSRF-Token", csrfToken);
  }

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers, credentials: "include" });
  } catch {
    throw new ApiError(0, "The Talvora API is not reachable.");
  }

  if (response.status === 204) return undefined as T;
  if (!response.ok) {
    const body = await readProblem(response);
    if (response.status === 403 && body?.code?.includes("CSRF")) clearCsrf();
    if (response.status === 401) clearCsrf();
    throw new ApiError(
      response.status,
      problemMessage(body, response.statusText || "Request failed"),
      body?.code,
    );
  }
  const text = await response.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

export async function startLogin(target = "/desk"): Promise<void> {
  const data = await api<{ authorization_url: string }>(
    `/api/v1/auth/login?redirect_target=${encodeURIComponent(target)}`,
  );
  window.location.assign(data.authorization_url);
}
