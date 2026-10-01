import { ACTIVE_STATUSES, api, type Run, type RunEvent } from "@/lib/api";
import {
  STATE_META, STATE_ORDER, deriveSteps, isPartial, lastEvent, needsHumanDecision,
  screenshotsOf, type Step, type UiState,
} from "@/lib/runState";
import styles from "./dashboard.module.css";

function Panel({ n, title, children, className }: {
  n: number; title: string; children: React.ReactNode; className?: string;
}) {
  return (
    <section className={`${styles.panel} ${className ?? ""}`}>
      <h2><span className={styles.num}>{n}</span>{title}</h2>
      {children}
    </section>
  );
}

const Empty = ({ children }: { children: React.ReactNode }) => <p className={styles.empty}>{children}</p>;

const offset = (run: Run, e: RunEvent) =>
  `+${((Date.parse(e.ts) - Date.parse(run.events[0].ts)) / 1000).toFixed(1)}s`;

// 3. Current status: what the agent is doing right now.
export function StatusPanel({ run, state }: { run: Run | null; state: UiState }) {
  const meta = STATE_META[state];
  const last = lastEvent(run);
  const active = !!run && ACTIVE_STATUSES.includes(run.status);
  const note = run?.status === "interrupted" ? "The backend restarted during this run. Resume to continue from where it stopped."
    : run?.pause_requested && run.status === "running" ? "Pause requested. The agent stops after its current step." : null;
  return (
    <Panel n={3} title="Current status" className={styles.statusPanel}>
      <div className={styles.statusHead}>
        <span className={`${styles.pill} ${styles[meta.tone]}`}>{state}</span>
        <span className={styles.blurb}>{note ?? meta.blurb}</span>
      </div>
      <div className={styles.nowBox}>
        <span className={styles.nowLabel}>{active ? "Doing now" : run ? "Last action" : "Doing now"}</span>
        <span className={styles.nowText}>{last ? last.message : "Nothing. Enter a goal and press Run agent."}</span>
      </div>
      <ol className={styles.rail} aria-label="Agent states">
        {STATE_ORDER.map((s) => (
          <li
            key={s}
            className={`${styles.railItem} ${s === state ? `${styles.railOn} ${styles[STATE_META[s].tone]}` : ""}`}
            aria-current={s === state ? "step" : undefined}
            title={STATE_META[s].blurb}
          >
            {s}
          </li>
        ))}
      </ol>
      {run && <p className={styles.meta}>Run {run.id}{run.scenario ? ` · scenario: ${run.scenario}` : ""} · {run.events.length} events</p>}
    </Panel>
  );
}

const STEP_LABEL: Record<Step["state"], string> = {
  pending: "Pending", active: "In progress", recovering: "Recovering",
  waiting: "Needs approval", done: "Done", failed: "Not completed",
};

// 5. Browser / action progress.
export function ProgressPanel({ run, state }: { run: Run | null; state: UiState }) {
  const steps = deriveSteps(run, state);
  return (
    <Panel n={5} title="Browser and action progress">
      <ol className={styles.steps}>
        {steps.map((s, i) => (
          <li key={s.key} className={`${styles.step} ${styles[`step_${s.state}`]}`}>
            <span className={styles.stepIdx}>{s.state === "done" ? "✓" : s.state === "failed" ? "✕" : i + 1}</span>
            <span className={styles.stepBody}>
              <span className={styles.stepLabel}>{s.label}</span>
              <span className={styles.stepDetail}>{s.detail}</span>
            </span>
            <span className={styles.stepState}>{STEP_LABEL[s.state]}</span>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

// 6. Approval panel. High-impact actions run only after a person approves the exact content.
export function ApprovalPanel({ run, busy, onDecide }: {
  run: Run | null; busy: boolean; onDecide: (approvalId: string, decision: "approve" | "reject") => void;
}) {
  const pending = run?.pending_approval ?? null;
  const decided = (run?.approvals ?? []).filter((a) => a.status === "approved" || a.status === "rejected");
  const d = pending?.details;
  return (
    <Panel n={6} title="Approval">
      {pending && d ? (
        <div className={`${styles.callout} ${styles.warn}`}>
          <strong>{d.title ?? "Approval required"}</strong>
          {d.action && <p>{d.action}</p>}
          {d.email && (
            <dl className={styles.email}>
              <div><dt>To</dt><dd>{d.email.to}</dd></div>
              <div><dt>Subject</dt><dd>{d.email.subject}</dd></div>
              <div><dt>Message</dt><dd className={styles.emailBody}>{d.email.message}</dd></div>
            </dl>
          )}
          {d.if_approved && <p className={styles.meta}><b>If approved:</b> {d.if_approved}</p>}
          {d.if_rejected && <p className={styles.meta}><b>If rejected:</b> {d.if_rejected}</p>}
          <div className={styles.actions}>
            <button className={styles.approve} disabled={busy} onClick={() => onDecide(pending.id, "approve")}>Approve and send</button>
            <button className={styles.secondary} disabled={busy} onClick={() => onDecide(pending.id, "reject")}>Reject</button>
          </div>
        </div>
      ) : needsHumanDecision(run) ? (
        <div className={`${styles.callout} ${styles.warn}`}>
          <strong>A person needs to decide</strong>
          <p>{run!.blocker}</p>
          <p className={styles.meta}>The agent changed nothing for this decision and has no way to proceed on its own.</p>
        </div>
      ) : (
        <Empty>Nothing is waiting for approval.</Empty>
      )}
      {decided.length > 0 && (
        <ul className={styles.checks}>
          {decided.map((a) => (
            <li key={a.id} className={styles.check}>
              <span className={`${styles.tag} ${a.status === "approved" ? styles.good : styles.bad}`}>{a.status.toUpperCase()}</span>
              <span>{a.details.title ?? a.action}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// 7. Failure and recovery: the failed-action -> check state -> decide chain.
const RECOVERY_TYPES = new Set(["ACTION_FAILED", "RECOVERY_STARTED", "RETRY"]);

export function RecoveryPanel({ run }: { run: Run | null }) {
  const events = (run?.events ?? []).filter(
    (e) => RECOVERY_TYPES.has(e.type) || (e.type === "STATE_CHECK" && e.data.phase === "post-failure"),
  );
  return (
    <Panel n={7} title="Failure and recovery">
      {events.length === 0 ? (
        <Empty>{run ? "No failures in this run." : "No failures yet."}</Empty>
      ) : (
        <ol className={styles.chain}>
          {events.map((e) => {
            const decision = typeof e.data.decision === "string" ? e.data.decision : null;
            const tone = e.type === "ACTION_FAILED" ? "bad" : e.type === "STATE_CHECK" ? "info" : "warn";
            return (
              <li key={e.seq} className={styles.chainItem}>
                <span className={`${styles.tag} ${styles[tone]}`}>{e.type.replace("_", " ")}</span>
                <span>{e.message}</span>
                {decision && <span className={styles.decision}>Decision: {decision.replace("_", " ")}</span>}
              </li>
            );
          })}
        </ol>
      )}
    </Panel>
  );
}

// 8. Verification results.
export function VerificationPanel({ run }: { run: Run | null }) {
  const checks = (run?.events ?? []).filter((e) => e.type === "VERIFICATION");
  return (
    <Panel n={8} title="Verification">
      {checks.length === 0 ? (
        <Empty>No checks yet. Results are read back from the app&apos;s own records.</Empty>
      ) : (
        <ul className={styles.checks}>
          {checks.map((e) => {
            const pass = e.data.passed === true;
            return (
              <li key={e.seq} className={styles.check}>
                <span className={`${styles.tag} ${pass ? styles.good : styles.bad}`}>{pass ? "PASS" : "FAIL"}</span>
                <span>{e.message}</span>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

// 9. Final task result.
export function ResultPanel({ run, state }: { run: Run | null; state: UiState }) {
  const meta = STATE_META[state];
  const details = run?.details ?? {};
  const rows: [string, string][] = [];
  if (typeof details.interview_id === "string") rows.push(["Interview", details.interview_id]);
  if (typeof details.invitation === "string") rows.push(["Invitation", details.invitation.replace("_", " ")]);
  if (typeof details.attempts === "number") rows.push(["Attempts", String(details.attempts)]);
  if (typeof details.created_by_agent === "boolean")
    rows.push(["Created by", details.created_by_agent ? "This run" : "Already existed (nothing duplicated)"]);

  return (
    <Panel n={9} title="Final result" className={styles.resultPanel}>
      {!run || ACTIVE_STATUSES.includes(run.status) || run.status === "interrupted" ? (
        <Empty>{run ? "In progress. The result appears here when the run ends." : "No result yet."}</Empty>
      ) : (
        <div className={`${styles.callout} ${styles[meta.tone]}`}>
          <strong>{meta.label}</strong>
          {run.status === "completed" || run.status === "rejected" || run.status === "stopped" ? (
            <p>{run.summary}</p>
          ) : (
            <>
              {isPartial(run) && <p>Earlier steps are done and verified, but a later step did not finish.</p>}
              <p><b>Blocker:</b> {run.blocker}</p>
            </>
          )}
          {rows.length > 0 && (
            <dl className={styles.kv}>
              {rows.map(([k, v]) => (
                <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
              ))}
            </dl>
          )}
        </div>
      )}
    </Panel>
  );
}

// 10. Evidence: screenshots the browser captured at each action.
export function EvidencePanel({ run }: { run: Run | null }) {
  const shots = screenshotsOf(run);
  return (
    <Panel n={10} title="Evidence">
      {shots.length === 0 ? (
        <Empty>No screenshots captured yet.</Empty>
      ) : (
        <ul className={styles.shots}>
          {shots.map(({ event, file }) => {
            const url = `${api.baseUrl}/screenshots/${encodeURIComponent(file)}`;
            const failed = file.includes("-fail");
            return (
              <li key={event.seq}>
                <a href={url} target="_blank" rel="noreferrer">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={url} alt={`Browser screenshot: ${event.message}`} loading="lazy" />
                </a>
                <span className={styles.shotCap}>
                  <span className={`${styles.tag} ${failed ? styles.bad : styles.good}`}>{failed ? "FAILED" : "OK"}</span>
                  {offset(run!, event)} · {event.type.replace("_", " ")}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

// 4. Agent activity timeline.
const TONE: Record<string, string> = {
  ACTION_FAILED: "bad", BLOCKED: "bad", RECOVERY_STARTED: "warn", RETRY: "warn",
  STATE_CHECK: "info", VERIFICATION: "info", ACTION_SUCCEEDED: "good", COMPLETED: "good",
  SKIPPED: "neutral", SETUP: "neutral", APPROVAL_REQUESTED: "warn", APPROVAL_GRANTED: "good",
  PAUSED: "warn", RESUMED: "info", RUN_RESTARTED: "warn", REJECTED: "warn", STOPPED: "neutral",
};

export function TimelinePanel({ run, starting }: { run: Run | null; starting: boolean }) {
  return (
    <Panel n={4} title="Agent activity timeline">
      {!run || run.events.length === 0 ? (
        <Empty>{starting ? "Starting the agent…" : "No activity yet."}</Empty>
      ) : (
        <ol className={styles.timeline}>
          {run.events.map((e, i) => (
            <li key={e.seq} className={`${styles.event} ${i === run.events.length - 1 && ACTIVE_STATUSES.includes(run.status) ? styles.eventLive : ""}`}>
              <time className={styles.eventTime}>{offset(run, e)}</time>
              <span className={`${styles.tag} ${styles[TONE[e.type] ?? "neutral"]}`}>{e.type.replace("_", " ")}</span>
              <span className={styles.eventMessage}>{e.message}</span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
