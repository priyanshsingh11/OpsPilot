"""Slot finding for batch scheduling.

The app stores only a start time, so every interview is treated as a 60-minute
block within working hours. A slot is free when the interviewer has no
scheduled interview overlapping it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

SLOT_MINUTES = 60
DAY_END = "18:00"


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def free_slots(count: int, date: str, start: str, interviewer: str,
               booked: list[tuple[str, str, str]]) -> list[str]:
    """Up to `count` free slot start times (YYYY-MM-DDTHH:MM), hourly from `start`.

    `booked` holds (scheduled_at, interviewer, status) for existing interviews.
    """
    busy = [t for ts, who, status in booked
            if status == "scheduled" and who.strip().lower() == interviewer.strip().lower()
            and (t := _parse(ts)) is not None]
    slot = datetime.fromisoformat(f"{date}T{start}")
    end_of_day = datetime.fromisoformat(f"{date}T{DAY_END}")
    length = timedelta(minutes=SLOT_MINUTES)
    out: list[str] = []
    while len(out) < count and slot + length <= end_of_day:
        if all(abs(slot - b) >= length for b in busy):
            out.append(slot.strftime("%Y-%m-%dT%H:%M"))
        slot += length
    return out
