// Presentation-only state derived from a run's event stream. The backend reports
// running | completed | blocked; the console shows a finer-grained operating state.
import type { Run, RunEvent } from "./api";

export type UiState =
  | "IDLE" | "PLANNING" | "EXECUTING" | "WAITING_FOR_APPROVAL" | "RECOVERING"
  | "VERIFYING" | "COMPLETED" | "PARTIALLY_COMPLETED" | "FAILED" | "PAUSED";

export type Tone = "neutral" | "info" | "warn" | "good" | "bad";

export const STATE_ORDER: UiState[] = [
  "IDLE", "PLANNING", "EXECUTING", "RECOVERING", "VERIFYING",
  "WAITING_FOR_APPROVAL", "PAUSED", "COMPLETED", "PARTIALLY_COMPLETED", "FAILED",
];

export const STATE_META: Record<UiState, { label: string; tone: Tone; blurb: string }> = {
  IDLE: { label: "Idle", tone: "neutral", blurb: "Waiting for a goal." },
  PLANNING: { label: "Planning", tone: "info", blurb: "Reading the goal and the app, then building a plan." },
  EXECUTING: { label: "Executing", tone: "info", blurb: "Operating the recruitment app in the browser." },
  WAITING_FOR_APPROVAL: { label: "Waiting for approval", tone: "warn", blurb: "Stopped before an action that needs a person's decision." },
  RECOVERING: { label: "Recovering", tone: "warn", blurb: "An action failed. Checking the app's real state before deciding what to do." },
  VERIFYING: { label: "Verifying", tone: "info", blurb: "Checking the app's records to confirm the result." },
  COMPLETED: { label: "Completed", tone: "good", blurb: "Goal achieved and verified." },
  PARTIALLY_COMPLETED: { label: "Partially completed", tone: "warn", blurb: "Some steps are done and verified; the rest could not be finished." },
  FAILED: { label: "Failed", tone: "bad", blurb: "The goal was not completed. See the blocker below." },
  PAUSED: { label: "Paused", tone: "neutral", blurb: "Run paused by an operator." },
};

// States this backend can actually produce. PAUSED is part of the console vocabulary
// but the agent has no pause control yet, so it is shown as reserved.
export const RESERVED_STATES: UiState[] = ["PAUSED"];

const RECOVERY_EVENTS = new Set(["ACTION_FAILED", "RECOVERY_STARTED", "RETRY"]);

export function lastEvent(run: Run | null): RunEvent | null {
  return run && run.events.length ? run.events[run.events.length - 1] : null;
}

// The agent signals "needs a person" by writing it into the blocker text (see runner.py
// and scheduling.py); there is no separate approval event.
export function needsApproval(run: Run | null): boolean {
  return run?.status === "blocked" && /needs your approval/i.test(run.blocker ?? "");
}

export function isPartial(run: Run | null): boolean {
  return run?.status === "blocked" && typeof run.details?.interview_id === "string";
}

export function deriveState(run: Run | null): UiState {
  if (!run) return "IDLE";
  if (run.status === "completed") return "COMPLETED";
  if (run.status === "blocked") {
    if (needsApproval(run)) return "WAITING_FOR_APPROVAL";
    return isPartial(run) ? "PARTIALLY_COMPLETED" : "FAILED";
  }
  const last = lastEvent(run);
  if (!run.events.some((e) => e.type === "PLAN")) return "PLANNING";
  if (last && RECOVERY_EVENTS.has(last.type)) return "RECOVERING";
  if (last?.type === "VERIFICATION") return "VERIFYING";
  return "EXECUTING";
}

export type StepState = "pending" | "active" | "recovering" | "waiting" | "done" | "failed";

export interface Step {
  key: string;
  label: string;
  detail: string;
  state: StepState;
}

const isVerification = (e: RunEvent, field: string) =>
  e.type === "VERIFICATION" && e.data[field] !== undefined && e.data.passed === true;

export function deriveSteps(run: Run | null, state: UiState): Step[] {
  const events = run?.events ?? [];
  const finished = !!run && run.status !== "running";
  const hasPlan = events.some((e) => e.type === "PLAN");
  const interviewVerified = events.some((e) => isVerification(e, "same_round_count"));
  const statusVerified = events.some((e) => isVerification(e, "expected"));
  const attempts = Math.max(0, ...events.map((e) => (typeof e.data.attempt === "number" ? e.data.attempt : 0)));
  const maxAttempts = Math.max(0, ...events.map((e) => (typeof e.data.max_attempts === "number" ? e.data.max_attempts : 0)));

  // The step that is unfinished when the run stops takes the terminal outcome.
  const stop: StepState = state === "WAITING_FOR_APPROVAL" ? "waiting" : "failed";
  const open = (): StepState => (!run ? "pending" : finished ? stop : "active");

  const read: StepState = hasPlan ? "done" : open();
  const schedule: StepState = !hasPlan ? "pending" : interviewVerified ? "done"
    : state === "RECOVERING" ? "recovering" : open();
  const status: StepState = !interviewVerified ? "pending" : statusVerified ? "done" : open();

  const scheduleDetail = !hasPlan ? "Schedule the interview form, then verify it exists"
    : attempts > 0 ? `Attempt ${attempts}${maxAttempts ? ` of ${maxAttempts}` : ""}`
    : "Check existing interviews, then submit the form";

  return [
    { key: "read", label: "Read goal and app state", detail: "Parse the goal, open candidate pages", state: read },
    { key: "schedule", label: "Schedule interview", detail: scheduleDetail, state: schedule },
    { key: "status", label: "Update candidate status", detail: "Set status to 'interview', then verify", state: status },
  ];
}

export function screenshotsOf(run: Run | null): { event: RunEvent; file: string }[] {
  return (run?.events ?? []).flatMap((event) =>
    typeof event.data.screenshot === "string"
      ? [{ event, file: event.data.screenshot.split("/").pop() as string }]
      : [],
  );
}
