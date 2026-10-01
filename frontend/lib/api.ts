const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface HealthResponse {
  status: "ok" | "degraded";
  service: string;
  version: string;
  environment: string;
  database: "ok" | "error";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store", ...init });
  if (!res.ok) {
    throw new Error(`Request to ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  baseUrl: API_URL,
  health: () => request<HealthResponse>("/api/health"),
};

export type EventType =
  | "GOAL_RECEIVED" | "PLAN" | "SETUP" | "STATE_CHECK" | "ACTION" | "ACTION_SUCCEEDED"
  | "ACTION_FAILED" | "RECOVERY_STARTED" | "RETRY" | "SKIPPED" | "VERIFICATION"
  | "COMPLETED" | "BLOCKED";

export interface RunEvent {
  seq: number;
  ts: string;
  type: EventType;
  message: string;
  data: Record<string, unknown>;
}

export interface Run {
  id: string;
  goal: string;
  scenario: string | null;
  status: "running" | "completed" | "blocked";
  summary: string | null;
  blocker: string | null;
  details: Record<string, unknown>;
  events: RunEvent[];
  created_at: string | null;
  finished_at: string | null;
}

export interface Scenario {
  key: string;
  label: string;
  description: string;
}

async function send<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().then((j) => j.detail).catch(() => res.statusText);
    throw new Error(typeof detail === "string" ? detail : `Request failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const agentApi = {
  scenarios: () => request<Scenario[]>("/api/scenarios"),
  startRun: (goal: string, scenario: string | null, resetDemoData: boolean) =>
    send<{ id: string }>("/api/runs", { goal, scenario, reset_demo_data: resetDemoData }),
  getRun: (id: string) => request<Run>(`/api/runs/${id}`),
};
