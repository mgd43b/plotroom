"""Render a lane brief from the project's current read-only facts."""

from dataclasses import dataclass
from string import Formatter

from plotroom.core.claims import Claim

BRIEF = "brief"
DEFAULT_TEMPLATE = """# Lane brief for {worker}

## Work item {work_item}

{source_text}

## Paths

{paths}

## Don't touch

{dont_touch}

## Plan

{plan}

## RESULT

{result}

## Standing rules

{standing_rules}
"""
_REQUIRED_FIELDS = frozenset(
    {"source_text", "paths", "dont_touch", "plan", "result", "standing_rules"}
)
_FIELDS = _REQUIRED_FIELDS | {"work_item", "worker"}


@dataclass(frozen=True)
class BriefSettings:
    template: str = DEFAULT_TEMPLATE
    plan: str = "Scope, implementation steps, and verification."
    result: str = "Status, changes, verification evidence, and any remaining blockers."
    standing_rules: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        fields: set[str] = set()
        for _, field, spec, conversion in Formatter().parse(self.template):
            if field is None:
                continue
            if field not in _FIELDS or spec or conversion is not None:
                raise ValueError(f"invalid brief template field {field!r}")
            fields.add(field)
        if missing := _REQUIRED_FIELDS - fields:
            raise ValueError(f"missing brief template fields: {', '.join(sorted(missing))}")

    def render(
        self,
        work_item: str,
        worker: str,
        source_text: str,
        lane: str | None,
        claims: list[Claim],
    ) -> str:
        own: list[str] = []
        others: list[str] = []
        for claim in claims:
            if claim.lane == lane:
                own.append(f"- {claim.glob} ({claim.reason})")
            else:
                others.append(f"- {claim.glob} ({claim.lane}: {claim.reason})")
        return self.template.format(
            work_item=work_item,
            worker=worker,
            source_text=source_text,
            paths="\n".join(own),
            dont_touch="\n".join(others),
            plan=self.plan,
            result=self.result,
            standing_rules="\n".join(f"- {rule}" for rule in self.standing_rules),
        )
