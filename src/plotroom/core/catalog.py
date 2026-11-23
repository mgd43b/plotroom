"""The catalog: the one place later stories register event kinds and commands."""

from __future__ import annotations

from dataclasses import dataclass

from plotroom.core.commands import CommandRegistry
from plotroom.core.events import EventRegistry
from plotroom.core.roles import WriterRules


@dataclass(frozen=True)
class Catalog:
    events: EventRegistry
    commands: CommandRegistry

    @property
    def writer_rules(self) -> WriterRules:
        return self.events.writer_rules


def build_catalog() -> Catalog:
    """Every kind and command the server knows. Empty until the stories that own them land."""
    events = EventRegistry()
    return Catalog(events, CommandRegistry(events))
