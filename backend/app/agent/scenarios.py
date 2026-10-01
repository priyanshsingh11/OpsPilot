"""Reproducible failure scenarios for demos and tests.

Each scenario arms the demo app's calendar failure simulation (demo-app/db.py) with a
fixed count and mode, so a run behaves the same way every time:

- none:             no failure.
- transient:        the first create fails before anything is saved; one safe retry succeeds.
- saved_but_failed: the first create saves the interview and still returns an error; a blind
                    retry would duplicate it, so the agent must detect it and not retry.
- outage:           every create fails; the agent stops with a concrete blocker after N attempts.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    description: str
    failing_attempts: int
    mode: str  # before_write | after_write


SCENARIOS: dict[str, Scenario] = {s.key: s for s in [
    Scenario("none", "Normal run", "No failure injected.", 0, "before_write"),
    Scenario("transient", "Calendar fails once",
             "The first create attempt fails before saving; the agent checks, then retries once.",
             1, "before_write"),
    Scenario("saved_but_failed", "Saved, but reported as failed",
             "The first create attempt saves the interview but returns a timeout; "
             "the agent must find it and NOT create a duplicate.", 1, "after_write"),
    Scenario("outage", "Calendar outage",
             "Every create attempt fails; the agent stops with a concrete blocker.", 10, "before_write"),
]}
