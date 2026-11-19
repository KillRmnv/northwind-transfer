from types import SimpleNamespace

import pytest
from pytest_check import check

from app.services.transfer_machine import EDGES, TERMINAL, TransferStateMachine


def machine_with(status: str) -> TransferStateMachine:
    return TransferStateMachine(session=None, transfer=SimpleNamespace(status=status))  # type: ignore[arg-type]


def test_edges_cover_every_known_status() -> None:
    with check:
        assert set(EDGES) == {"PENDING", "PROCESSING", "NEEDS_REVIEW", "COMPLETED", "FAILED"}


def test_terminal_states_have_no_outgoing_edges() -> None:
    for status in TERMINAL:
        with check:
            assert EDGES[status] == frozenset()


def test_every_edge_target_is_known() -> None:
    for source, targets in EDGES.items():
        for target in targets:
            with check:
                assert target in EDGES, f"{source} -> {target}"


@pytest.mark.parametrize(
    ("source", "target", "allowed"),
    [
        ("PENDING", "PROCESSING", True),
        ("PENDING", "COMPLETED", True),
        ("PENDING", "FAILED", True),
        ("PENDING", "NEEDS_REVIEW", True),
        ("PROCESSING", "COMPLETED", True),
        ("PROCESSING", "FAILED", True),
        ("PROCESSING", "NEEDS_REVIEW", True),
        ("NEEDS_REVIEW", "PROCESSING", True),
        ("NEEDS_REVIEW", "COMPLETED", True),
        ("NEEDS_REVIEW", "FAILED", True),
        ("PROCESSING", "PENDING", False),
        ("NEEDS_REVIEW", "PENDING", False),
        ("COMPLETED", "FAILED", False),
        ("FAILED", "PROCESSING", False),
        ("COMPLETED", "COMPLETED", False),
        ("UNKNOWN", "PROCESSING", False),
    ],
)
def test_can_follows_transition_table(source: str, target: str, allowed: bool) -> None:
    assert machine_with(source).can(target) is allowed
