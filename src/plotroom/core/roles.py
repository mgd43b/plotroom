"""Roles (AD-8): who an input comes from. ``<role>:<request_id>`` is unambiguous because no role
contains more than one colon and a worker slug contains none."""

from __future__ import annotations

import re

SIMPLE_ROLES: frozenset[str] = frozenset({"orchestrator", "clerk", "drain", "bridge", "system"})
WORKER_FAMILY = "worker"
_WORKER = re.compile(r"worker:([a-z0-9](?:[a-z0-9_-]*[a-z0-9])?)")


def role_family(role: str) -> str:
    """The role family a registry names: ``worker:<slug>`` is ``worker``, the rest are themselves.

    Raises ``ValueError`` for anything outside the role grammar.
    """
    if role in SIMPLE_ROLES:
        return role
    if _WORKER.fullmatch(role):
        return WORKER_FAMILY
    raise ValueError(
        f"invalid role {role!r}: expected one of {sorted(SIMPLE_ROLES)} or worker:<slug>"
    )
