"use client";

import { useEffect, useRef, useState } from "react";
import { agentApi, api, type HealthResponse, type Run, type RunEvent, type Scenario } from "@/lib/api";
import styles from "./dashboard.module.css";

type BackendState =
  | { kind: "checking" }
  | { kind: "online"; health: HealthResponse }
  | { kind: "offline"; error: string };

const EXAMPLE_GOAL =
  "Schedule a Technical Screen for Aarav Sharma with Priya Nair on 2026-10-08 at 10:00";

// Visual grouping of event types in the timeline.
const EVENT_TONE: Record<string, string> = {
  ACTION_FAILED: "bad",
  BLOCKED: "bad",
  RECOVERY_STARTED: "warn",
  RETRY: "warn",
  STATE_CHECK: "info",
  VERIFICATION: "info",
  ACTION_SUCCEEDED: "good",
  COMPLETED: "good",
  SKIPPED: "muted",
  SETUP: "muted",
};

function EventRow({ event }: { event: RunEvent }) {
  const tone = EVENT_TONE[event.type] ?? "neutral";
  const shot = typeof event.data.screenshot === "string" ? event.data.screenshot : null;
  return (
    <li className={styles.event}>
      <span className={`${styles.eventType} ${styles[tone]}`}>{event.type}</span>
      <span className={styles.eventMessage}>
        {event.message}
        {shot && <span className={styles.shot}>screenshot: {shot.split("/").pop()}</span>}
      </span>
      <time className={styles.eventTime}>{new Date(event.ts).toLocaleTimeString()}</time>
    </li>
  );
}

export default function Dashboard() {
  const [goal, setGoal] = useState(EXAMPLE_GOAL);
  const [backend, setBackend] = useState<BackendState>({ kind: "checking" });
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenario, setScenario] = useState("none");
  const [resetData, setResetData] = useState(true);
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
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
  const running = run?.status === "running";

  async function startRun() {
    setError(null);
    try {
      const { id } = await agentApi.startRun(goal, scenario, resetData);
      setRun({ id, goal, scenario, status: "running", summary: null, blocker: null,
        details: {}, events: [], created_at: null, finished_at: null });
      if (poll.current) clearInterval(poll.current);
      poll.current = setInterval(async () => {
        try {
          const latest = await agentApi.getRun(id);
          setRun(latest);
          if (latest.status !== "running" && poll.current) clearInterval(poll.current);
        } catch (e) {
          setError((e as Error).message);
        }
      }, 500);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const selected = scenarios.find((s) => s.key === scenario);

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <h1>OpsPilot</h1>
        <span className={`${styles.badge} ${styles[backend.kind]}`}>
          {backend.kind === "checking" && "Checking backend…"}
          {backend.kind === "online" && `Backend ${backend.health.status} · v${backend.health.version}`}
          {backend.kind === "offline" && "Backend offline"}
        </span>
      </header>

      <section className={styles.card}>
        <label htmlFor="goal">Goal</label>
        <textarea
          id="goal"
          rows={3}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Schedule a <round> for <candidate> with <interviewer> on <date> at <time>"
        />
        <div className={styles.controls}>
          <label htmlFor="scenario">Failure scenario</label>
          <select id="scenario" value={scenario} onChange={(e) => setScenario(e.target.value)}>
            {scenarios.map((s) => (
              <option key={s.key} value={s.key}>{s.label}</option>
            ))}
          </select>
          <label className={styles.check}>
            <input type="checkbox" checked={resetData} onChange={(e) => setResetData(e.target.checked)} />
            Reset demo data first
          </label>
        </div>
        {selected && <p className={styles.hint}>{selected.description}</p>}
        <button disabled={!online || running || goal.trim().length < 3} onClick={startRun}>
          {running ? "Running…" : "Run Agent"}
        </button>
        {error && <p className={styles.error}>{error}</p>}
      </section>

      <div className={styles.grid}>
        <section className={styles.card}>
          <h2>Execution Status</h2>
          {backend.kind === "offline" ? (
            <p className={styles.error}>{backend.error}</p>
          ) : !run ? (
            <p>{online ? "Idle — backend connected." : "Connecting…"}</p>
          ) : (
            <>
              <p>
                <span className={`${styles.status} ${styles[run.status]}`}>{run.status}</span>
              </p>
              <p className={styles.hint}>
                Run {run.id} · {run.events.length} event(s)
                {run.scenario ? ` · scenario: ${run.scenario}` : ""}
              </p>
            </>
          )}
        </section>

        <section className={styles.card}>
          <h2>Result</h2>
          {!run || run.status === "running" ? (
            <p className={styles.empty}>No result yet.</p>
          ) : run.status === "completed" ? (
            <p>{run.summary}</p>
          ) : (
            <p className={styles.error}><strong>Blocked:</strong> {run.blocker}</p>
          )}
        </section>
      </div>

      <section className={styles.card}>
        <h2>Activity Timeline</h2>
        {!run || run.events.length === 0 ? (
          <p className={styles.empty}>{running ? "Starting…" : "No activity yet."}</p>
        ) : (
          <ol className={styles.timeline}>
            {run.events.map((e) => (
              <EventRow key={e.seq} event={e} />
            ))}
          </ol>
        )}
      </section>
    </main>
  );
}
