"""Which of the open slots Gus offers: a few, spread over days."""

from collections.abc import Sequence
from datetime import date, tzinfo

from gvas.scheduling.core.model import Slot

MAX_OFFERS = 3


def spread_offers(slots: Sequence[Slot], zone: tzinfo, limit: int = MAX_OFFERS) -> tuple[Slot, ...]:
    """At most ``limit`` slots, taking the earliest of each day in turn so
    the offer covers at least two days whenever two days have openings."""

    by_day: dict[date, list[Slot]] = {}
    for slot in sorted(slots, key=lambda candidate: candidate.start):
        by_day.setdefault(slot.start.astimezone(zone).date(), []).append(slot)
    picked: list[Slot] = []
    queues = list(by_day.values())
    while len(picked) < limit and any(queues):
        for queue in queues:
            if queue and len(picked) < limit:
                picked.append(queue.pop(0))
    return tuple(sorted(picked, key=lambda slot: slot.start))
