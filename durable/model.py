"""Core vocabulary: run states and the transitions the store is allowed to make.

The transition table is the contract. If a new state or edge is needed, edit
``docs/design.md`` §4 first, then this file, then the tests.
"""

from __future__ import annotations

from enum import Enum
from typing import FrozenSet, Mapping


class State(str, Enum):
    """Lifecycle of a single *job run* (one attempt of one job)."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


#: States from which a job run can never move again.
TERMINAL: FrozenSet[State] = frozenset(
    {State.SUCCEEDED, State.FAILED, State.SKIPPED, State.CANCELLED}
)

#: ``INTERRUPTED`` is deliberately *not* terminal: it is the crash marker that
#: recovery turns back into ``READY`` (or ``FAILED`` when attempts run out).
ALLOWED: Mapping[State, FrozenSet[State]] = {
    State.PENDING: frozenset({State.READY, State.SKIPPED, State.CANCELLED}),
    State.READY: frozenset({State.RUNNING, State.CANCELLED}),
    State.RUNNING: frozenset(
        {
            State.SUCCEEDED,
            State.FAILED,
            State.RETRYING,
            State.INTERRUPTED,
            State.CANCELLED,
        }
    ),
    State.RETRYING: frozenset({State.READY, State.CANCELLED}),
    State.INTERRUPTED: frozenset({State.READY, State.FAILED, State.CANCELLED}),
    State.SUCCEEDED: frozenset(),
    State.FAILED: frozenset(),
    State.SKIPPED: frozenset(),
    State.CANCELLED: frozenset(),
}


class InvalidTransition(Exception):
    """Raised when code tries to move a run along an edge that is not allowed."""

    def __init__(self, current: State, target: State) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move a job run from {current.value!r} to {target.value!r}")


def can_transition(current: State, target: State) -> bool:
    """True when ``current -> target`` is a legal edge."""

    return target in ALLOWED[current]


def require_transition(current: State, target: State) -> State:
    """Return ``target`` if the edge is legal, otherwise raise ``InvalidTransition``."""

    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    return target
