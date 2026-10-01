// Presentation-only state derived from a run's event stream. The backend reports
// running | completed | blocked; the console shows a finer-grained operating state.
import { ACTIVE_STATUSES, type Run, type RunEvent } from "./api";

const ACTIVE: string[] = ACTIVE_STATUSES;

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
  WAITING_FOR_APPROVAL: { label: "Waiting for approval", tone: "warn", blurb: "The agent is holding before a high-impact action. Review it and approve or reject." },
  RECOVERING: { label: "Recovering", tone: "warn", blurb: "An action failed. Checking the app's real state before deciding what to do." },
  VERIFYING: { label: "Verifying", tone: "info", blurb: "Checking the app's records to confirm the result." },
  COMPLETED: { label: "Completed", tone: "good", blurb: "Goal achieved and verified." },
  PARTIALLY_COMPLETED: { label: "Partially completed", tone: "warn", blurb: "Some steps are done and verified; the rest could not be finished." },
  FAILED: { label: "Failed", tone: "bad", blurb: "The goal was not completed. See the blocker below." },
  PAUSED: { label: "Paused", tone: "neutral", blurb: "Nothing is executing. Continue or stop the run." },
};

// PAUSED covers both an operator pause and a run interrupted by a backend restart:
// in either case nothing is executing and the operator can continue or stop it.
export const RESERVED_STATES: UiState[] = [];

const RECOVERY_EVENTS = new Set(["ACTION_FAILED", "RECOVERY_STARTED", "RETRY"]);

export function lastEvent(run: Run | null): RunEvent | null {
  return run && run.events.length ? run.events[run.events.length - 1] : null;
}

// Dead-end blockers (rejected candidate, already-booked round) say a person must decide,
// but the agent has no approval channel for them. Shown as a note, not as a pending approval.
export function needsHumanDecision(run: Run | null): boolean {
  return run?.status === "blocked" && /needs your approval/i.test(run.blocker ?? "");
}

const stepsDone = (run: Run) =>
  typeof run.details?.interview_id === "string" ||
  Object.values((run.details?.steps as Record<string, string> | undefined) ?? {}).includes("done");

export function isPartial(run: Run | null): boolean {
  if (run?.status === "partially_completed") return true;
  return !!run && ["blocked", "stopped", "rejected"].includes(run.status) && stepsDone(run);
}

export function deriveState(run: Run | null): UiState {
  if (!run) return "IDLE";
  switch (run.status) {
    case "completed": return "COMPLETED";
    case "partially_completed": return "PARTIALLY_COMPLETED";
    case "failed": return "FAILED";
    case "awaiting_approval": return "WAITING_FOR_APPROVAL";
    case "paused":
    case "interrupted": return "PAUSED";
    case "blocked":
    case "stopped":
    case "rejected": return isPartial(run) ? "PARTIALLY_COMPLETED" : "FAILED";
  }
  const last = lastEvent(run);
  // The approval is recorded a moment before the run's status flips; don't show EXECUTING in that gap.
  if (run.pending_approval) return "WAITING_FOR_APPROVAL";
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

interface PlanTarget { id: string; name: string; slot: string | null }

// Batch runs ("all shortlisted X candidates"): one step per candidate, then invitations and the final check.
function deriveBatchSteps(run: Run, plan: RunEvent, state: UiState): Step[] {
  const events = run.events;
  const finished = !ACTIVE.includes(run.status);
  const targets = (plan.data.targets as PlanTarget[] | undefined) ?? [];
  const ids = (type: string) => new Set(events.filter((e) => e.type === type).map((e) => e.data.candidate_id));
  const blocked = ids("TARGET_BLOCKED");
  const done = ids("TARGET_DONE");
  // Only the first unfinished step is live; later ones are pending (or failed once the run is over).
  let liveTaken = false;
  const open = (): StepState => {
    if (finished) return "failed";
    if (liveTaken) return "pending";
    liveTaken = true;
    return state === "WAITING_FOR_APPROVAL" ? "waiting" : state === "RECOVERING" ? "recovering" : "active";
  };
  const steps: Step[] = [
    { key: "plan", label: "Find candidates and free slots", detail: `${targets.length} candidate(s) found`, state: "done" },
  ];
  for (const t of targets) {
    steps.push({
      key: t.id,
      label: `Schedule ${t.name}`,
      detail: t.slot ? `${t.slot.replace("T", " ")} · then status 'interview'` : "No free slot",
      state: blocked.has(t.id) ? "failed" : done.has(t.id) ? "done" : open(),
    });
  }
  const finalChecks = events.filter((e) => e.type === "VERIFICATION" && e.data.final);
  if (/approval before each invitation/i.test(plan.message)) {
    const sent = events.filter((e) => e.type === "VERIFICATION" && e.data.invitation_ids !== undefined && e.data.passed === true).length;
    const owed = targets.length - blocked.size;
    steps.push({ key: "invite", label: "Send invitations", detail: `${sent} of ${owed} sent, each after your approval`,
      state: sent >= owed && owed > 0 ? "done" : finalChecks.length ? "failed" : open() });
  }
  steps.push({ key: "verify", label: "Independent verification", detail: "Compare the app's records with the goal",
    state: finalChecks.length ? (finalChecks.every((e) => e.data.passed) ? "done" : "failed") : open() });
  return steps;
}

export function deriveSteps(run: Run | null, state: UiState): Step[] {
  const events = run?.events ?? [];
  const finished = !!run && !ACTIVE.includes(run.status);
  const plan = events.find((e) => e.type === "PLAN");
  if (run && plan?.data.batch) return deriveBatchSteps(run, plan, state);
  const withInvite = /invitation/i.test(plan?.message ?? "");
  const interviewVerified = events.some((e) => isVerification(e, "same_round_count"));
  const statusVerified = events.some((e) => isVerification(e, "expected"));
  const inviteVerified = events.some((e) => isVerification(e, "invitation_ids"));
  const attempts = Math.max(0, ...events.map((e) => (typeof e.data.attempt === "number" ? e.data.attempt : 0)));
  const maxAttempts = Math.max(0, ...events.map((e) => (typeof e.data.max_attempts === "number" ? e.data.max_attempts : 0)));

  // The first unfinished step takes the live (or terminal) condition of the run.
  const open = (): StepState => (!run ? "pending" : finished ? "failed"
    : state === "WAITING_FOR_APPROVAL" ? "waiting" : state === "RECOVERING" ? "recovering" : "active");

  const read: StepState = plan ? "done" : open();
  const schedule: StepState = !plan ? "pending" : interviewVerified ? "done" : open();
  const status: StepState = !interviewVerified ? "pending" : statusVerified ? "done" : open();
  const invite: StepState = !statusVerified ? "pending" : inviteVerified ? "done" : open();

  const scheduleDetail = attempts > 0 ? `Attempt ${attempts}${maxAttempts ? ` of ${maxAttempts}` : ""}`
    : "Check existing interviews, then submit the form";

  const steps: Step[] = [
    { key: "read", label: "Read goal and app state", detail: "Parse the goal, open candidate pages", state: read },
    { key: "schedule", label: "Schedule interview", detail: scheduleDetail, state: schedule },
    { key: "status", label: "Update candidate status", detail: "Set status to 'interview', then verify", state: status },
  ];
  if (withInvite)
    steps.push({ key: "invite", label: "Send invitation email", detail: "Only after you approve the exact email", state: invite });
  return steps;
}

export function screenshotsOf(run: Run | null): { event: RunEvent; file: string }[] {
  return (run?.events ?? []).flatMap((event) =>
    typeof event.data.screenshot === "string"
      ? [{ event, file: event.data.screenshot.split("/").pop() as string }]
      : [],
  );
}
