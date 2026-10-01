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
  | "COMPLETED" | "BLOCKED" | "APPROVAL_REQUESTED" | "APPROVAL_GRANTED" | "PAUSED"
  | "RESUMED" | "RUN_RESTARTED" | "REJECTED" | "STOPPED"
  | "TARGET_BLOCKED" | "TARGET_DONE" | "EVIDENCE" | "PARTIALLY_COMPLETED" | "FAILED";

export interface RunEvent {
  seq: number;
  ts: string;
  type: EventType;
  message: string;
  data: Record<string, unknown>;
}

export type RunStatus =
  | "running" | "paused" | "awaiting_approval" | "interrupted"
  | "completed" | "partially_completed" | "failed" | "blocked" | "stopped" | "rejected";

// Statuses in which the agent thread is alive and the dashboard should keep polling.
export const ACTIVE_STATUSES: RunStatus[] = ["running", "paused", "awaiting_approval"];

export interface Approval {
  id: string;
  action: string;
  status: "pending" | "approved" | "rejected" | "cancelled";
  details: {
    title?: string;
    action?: string;
    candidates?: { id: string; name: string; email: string; status: string }[];
    email?: { to: string; subject: string; message: string };
    if_approved?: string;
    if_rejected?: string;
  };
}

export interface Run {
  id: string;
  goal: string;
  scenario: string | null;
  status: RunStatus;
  approvals?: Approval[];
  pending_approval?: Approval | null;
  pause_requested?: boolean;
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
  pause: (id: string) => send<Run>(`/api/runs/${id}/pause`, {}),
  continueRun: (id: string) => send<Run>(`/api/runs/${id}/continue`, {}),
  stop: (id: string) => send<Run>(`/api/runs/${id}/stop`, {}),
  resume: (id: string) => send<Run>(`/api/runs/${id}/resume`, {}),
  approve: (id: string, approvalId: string) => send<Run>(`/api/runs/${id}/approvals/${approvalId}/approve`, {}),
  reject: (id: string, approvalId: string) => send<Run>(`/api/runs/${id}/approvals/${approvalId}/reject`, {}),
};
