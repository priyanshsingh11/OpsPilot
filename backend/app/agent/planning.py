"""Slot finding for batch scheduling.

A slot is free when it fits inside the requested window and does not overlap any
scheduled interview of the same interviewer (each booking blocks its own duration).
Slots are packed back to back from the start of the window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class Booking:
    scheduled_at: str
    duration_minutes: int
    interviewer: str
    status: str


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def free_slots(count: int, date: str, window_start: str, window_end: str, duration_minutes: int,
               interviewer: str, booked: list[Booking], step_minutes: int = 15) -> list[str]:
    """Up to `count` non-overlapping slot starts (YYYY-MM-DDTHH:MM) for `interviewer`."""
    length = timedelta(minutes=duration_minutes)
    busy = [(t, t + timedelta(minutes=b.duration_minutes)) for b in booked
            if b.status == "scheduled" and b.interviewer.strip().lower() == interviewer.strip().lower()
            and (t := _parse(b.scheduled_at)) is not None]
    slot = datetime.fromisoformat(f"{date}T{window_start}")
    end = datetime.fromisoformat(f"{date}T{window_end}")
    out: list[str] = []
    while len(out) < count and slot + length <= end:
        clash = next((b_end for b_start, b_end in busy if slot < b_end and b_start < slot + length), None)
        if clash is None:
            out.append(slot.strftime("%Y-%m-%dT%H:%M"))
            busy.append((slot, slot + length))
            slot += length
        else:
            # Jump past the clashing booking, aligned to the step grid.
            minutes = int((clash - slot).total_seconds() // 60)
            slot += timedelta(minutes=-(-minutes // step_minutes) * step_minutes)
    return out
