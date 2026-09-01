from __future__ import annotations

from fixora.http.protocol import ACTIVE_STATUSES, EVENT_TYPES, TERMINAL_STATUSES, WAITING_STATUSES
from fixora.tasks.attempts import (
    ACTIVE_STATUSES as ATTEMPT_ACTIVE,
)
from fixora.tasks.attempts import (
    TERMINAL_STATUSES as ATTEMPT_TERMINAL,
)
from fixora.tasks.attempts import (
    WAITING_STATUSES as ATTEMPT_WAITING,
)


def test_protocol_status_sets_match_attempts() -> None:
    assert ACTIVE_STATUSES == ATTEMPT_ACTIVE
    assert WAITING_STATUSES == ATTEMPT_WAITING
    assert TERMINAL_STATUSES == ATTEMPT_TERMINAL
    assert not (ACTIVE_STATUSES & WAITING_STATUSES)
    assert not (ACTIVE_STATUSES & TERMINAL_STATUSES)


def test_protocol_lists_control_plane_events() -> None:
    assert "task.started" in EVENT_TYPES
    assert "approval.required" in EVENT_TYPES
    assert "agent.tool" in EVENT_TYPES
