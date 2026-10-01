"use client";

import { useEffect, useState } from "react";
import { api, type HealthResponse } from "@/lib/api";
import styles from "./dashboard.module.css";

type BackendState =
  | { kind: "checking" }
  | { kind: "online"; health: HealthResponse }
  | { kind: "offline"; error: string };

export default function Dashboard() {
  const [goal, setGoal] = useState("");
  const [backend, setBackend] = useState<BackendState>({ kind: "checking" });

  useEffect(() => {
    api
      .health()
      .then((health) => setBackend({ kind: "online", health }))
      .catch((e: Error) => setBackend({ kind: "offline", error: e.message }));
  }, []);

  const online = backend.kind === "online";

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
          rows={4}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Describe what you want the agent to do…"
        />
        <button disabled title="The agent is not implemented yet (Phase 1)">
          Run Agent
        </button>
        <p className={styles.hint}>Agent execution arrives in a later phase.</p>
      </section>

      <div className={styles.grid}>
        <section className={styles.card}>
          <h2>Execution Status</h2>
          {backend.kind === "offline" ? (
            <p className={styles.error}>{backend.error}</p>
          ) : (
            <p>{online ? "Idle — backend connected." : "Connecting…"}</p>
          )}
        </section>

        <section className={styles.card}>
          <h2>Activity Timeline</h2>
          <p className={styles.empty}>No activity yet.</p>
        </section>
      </div>

      <section className={styles.card}>
        <h2>Result</h2>
        <p className={styles.empty}>No result yet.</p>
      </section>
    </main>
  );
}
