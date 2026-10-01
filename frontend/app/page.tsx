"use client";

import { useEffect, useRef, useState } from "react";
import { ACTIVE_STATUSES, agentApi, api, type HealthResponse, type Run, type Scenario } from "@/lib/api";
import { deriveState } from "@/lib/runState";
import {
  ApprovalPanel, EvidencePanel, ProgressPanel, RecoveryPanel, ResultPanel,
  StatusPanel, TimelinePanel, VerificationPanel,
} from "./panels";
import styles from "./dashboard.module.css";

type BackendState =
  | { kind: "checking" }
  | { kind: "online"; health: HealthResponse }
  | { kind: "offline"; error: string };

const EXAMPLE_GOAL =
  "Schedule a Technical Screen for Aarav Sharma with Priya Nair on 2026-10-08 at 10:00";

export default function Dashboard() {
  const [goal, setGoal] = useState(EXAMPLE_GOAL);
  const [backend, setBackend] = useState<BackendState>({ kind: "checking" });
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenario, setScenario] = useState("none");
  const [resetData, setResetData] = useState(true);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    api
      .health()
      .then((health) => setBackend({ kind: "online", health }))
      .catch((e: Error) => setBackend({ kind: "offline", error: e.message }));
    agentApi.scenarios().then(setScenarios).catch(() => setScenarios([]));
    return () => {
      if (poll.current) clearInterval(poll.current);
    };
  }, []);

  const online = backend.kind === "online";
  const active = !!run && ACTIVE_STATUSES.includes(run.status);
  const state = deriveState(run);

  // Poll until the run reaches a status where nothing is executing (finished or interrupted).
  function watch(id: string) {
    if (poll.current) clearInterval(poll.current);
    poll.current = setInterval(async () => {
      try {
        const latest = await agentApi.getRun(id);
        setRun(latest);
        if (!ACTIVE_STATUSES.includes(latest.status) && poll.current) clearInterval(poll.current);
      } catch (e) {
        setError((e as Error).message);
      }
    }, 500);
  }

  async function startRun() {
    setError(null);
    try {
      const { id } = await agentApi.startRun(goal, scenario, resetData);
      setRun({ id, goal, scenario, status: "running", summary: null, blocker: null,
        details: {}, events: [], created_at: null, finished_at: null });
      watch(id);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // Operator controls (pause, continue, stop, resume, approve, reject). Each returns the updated run.
  async function control(action: () => Promise<Run>, rewatch = false) {
    setError(null);
    setBusy(true);
    try {
      const updated = await action();
      setRun(updated);
      if (rewatch || ACTIVE_STATUSES.includes(updated.status)) watch(updated.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const decide = (approvalId: string, decision: "approve" | "reject") =>
    control(() => (decision === "approve" ? agentApi.approve(run!.id, approvalId) : agentApi.reject(run!.id, approvalId)));

  const selected = scenarios.find((s) => s.key === scenario);

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1>OpsPilot</h1>
          <p className={styles.tagline}>AI operations console · recruitment workflow</p>
        </div>
        <span className={`${styles.badge} ${styles[backend.kind]}`}>
          {backend.kind === "checking" && "Checking backend…"}
          {backend.kind === "online" && `Backend ${backend.health.status} · v${backend.health.version}`}
          {backend.kind === "offline" && "Backend offline"}
        </span>
      </header>
      {backend.kind === "offline" && <p className={styles.error}>{backend.error}</p>}

      <section className={`${styles.panel} ${styles.goalPanel}`}>
        <h2><span className={styles.num}>1</span>Goal</h2>
        <textarea
          id="goal"
          aria-label="Goal"
          rows={2}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Schedule a <round> for <candidate> with <interviewer> on <date> at <time>"
        />
        <div className={styles.controls}>
          <div className={styles.field}>
            <label htmlFor="scenario">Failure scenario</label>
            <select id="scenario" value={scenario} onChange={(e) => setScenario(e.target.value)}>
              {scenarios.map((s) => (
                <option key={s.key} value={s.key}>{s.label}</option>
              ))}
            </select>
          </div>
          <label className={styles.checkbox}>
            <input type="checkbox" checked={resetData} onChange={(e) => setResetData(e.target.checked)} />
            Reset demo data first
          </label>
          <div className={styles.runBar}>
            {run && run.status === "interrupted" && (
              <button className={styles.secondary} disabled={busy} onClick={() => control(() => agentApi.resume(run.id), true)}>Resume</button>
            )}
            {run && run.status === "running" && !run.pause_requested && (
              <button className={styles.secondary} disabled={busy} onClick={() => control(() => agentApi.pause(run.id))}>Pause</button>
            )}
            {run && (run.status === "paused" || (run.status === "running" && run.pause_requested)) && (
              <button className={styles.secondary} disabled={busy} onClick={() => control(() => agentApi.continueRun(run.id))}>Continue</button>
            )}
            {run && (active || run.status === "interrupted") && (
              <button className={styles.secondary} disabled={busy} onClick={() => control(() => agentApi.stop(run.id))}>Stop</button>
            )}
            <button className={styles.run} disabled={!online || active || goal.trim().length < 3} onClick={startRun}>
              {active ? "Running…" : "Run agent"}
            </button>
          </div>
        </div>
        {selected && <p className={styles.meta}>{selected.description}</p>}
        {error && <p className={styles.error}>{error}</p>}
      </section>

      <StatusPanel run={run} state={state} />
      <ResultPanel run={run} state={state} />

      <div className={styles.columns}>
        <TimelinePanel run={run} starting={active} />
        <div className={styles.side}>
          <ProgressPanel run={run} state={state} />
          <ApprovalPanel run={run} busy={busy} onDecide={decide} />
          <RecoveryPanel run={run} />
          <VerificationPanel run={run} />
        </div>
      </div>

      <EvidencePanel run={run} />
    </main>
  );
}
