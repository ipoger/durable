"""Tests for the state machine contract."""

from __future__ import annotations

import unittest

from durable.model import (
    ALLOWED,
    TERMINAL,
    InvalidTransition,
    State,
    can_transition,
    require_transition,
)


class Transitions(unittest.TestCase):
    def test_happy_path_is_legal(self) -> None:
        path = [State.PENDING, State.READY, State.RUNNING, State.SUCCEEDED]
        for current, target in zip(path, path[1:]):
            self.assertTrue(can_transition(current, target), f"{current} -> {target}")

    def test_retry_loop_is_legal(self) -> None:
        self.assertTrue(can_transition(State.RUNNING, State.RETRYING))
        self.assertTrue(can_transition(State.RETRYING, State.READY))
        self.assertTrue(can_transition(State.READY, State.RUNNING))

    def test_cannot_skip_from_pending_to_running(self) -> None:
        self.assertFalse(can_transition(State.PENDING, State.RUNNING))

    def test_terminal_states_have_no_exit(self) -> None:
        for state in TERMINAL:
            self.assertEqual(ALLOWED[state], frozenset(), state)

    def test_interrupted_is_recoverable(self) -> None:
        self.assertNotIn(State.INTERRUPTED, TERMINAL)
        self.assertTrue(can_transition(State.INTERRUPTED, State.READY))
        self.assertTrue(can_transition(State.INTERRUPTED, State.FAILED))

    def test_every_state_is_in_the_table(self) -> None:
        self.assertEqual(set(ALLOWED), set(State))

    def test_require_transition_raises_with_context(self) -> None:
        with self.assertRaises(InvalidTransition) as caught:
            require_transition(State.SUCCEEDED, State.RUNNING)
        self.assertEqual(caught.exception.current, State.SUCCEEDED)
        self.assertEqual(caught.exception.target, State.RUNNING)
        self.assertIn("succeeded", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
