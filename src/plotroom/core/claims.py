"""Lane path reservations and claim overlap rules."""

from __future__ import annotations

import json
import re
from bisect import bisect_left
from collections import defaultdict
from dataclasses import asdict, dataclass
from itertools import combinations
from typing import TYPE_CHECKING, Any

from plotroom.core.events import LedgerError

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

CLAIM_OVERLAP = "claim_overlap"
PR_CLAIM_OVERLAP = "pr_claim_overlap"
TICKET_TREE_MOVE = "ticket_tree_move"
OVERLAP_KINDS = (CLAIM_OVERLAP, PR_CLAIM_OVERLAP, TICKET_TREE_MOVE)
_GLOB_TOKEN = re.compile(r"\\(.)|([*?\[])")
_TRAILING_ESCAPE = re.compile(r"(?<!\\)(?:\\\\)*\\$")
_TRAILING_SPACES = re.compile(r"(?<!\\)((?:\\\\)*)( +)$")
_BRACKET_PART = r"(?:\\.|\[:[a-z]+:\]|[^\\\]])"
_BRACKET = re.compile(rf"\[(?P<negate>[!^]?+)(?P<contents>\]{_BRACKET_PART}*|{_BRACKET_PART}+)\]")
_CLASS_TOKEN = re.compile(r"\\(.)|\[:([a-z]+):\]|(.)")
_POSIX_CLASSES = {
    "alnum": "0-9A-Za-z",
    "alpha": "A-Za-z",
    "blank": r"\x09\x20",
    "cntrl": r"\x00-\x1f\x7f",
    "digit": "0-9",
    "graph": r"\x21-\x7e",
    "lower": "a-z",
    "print": r"\x20-\x7e",
    "punct": r"\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e",
    "space": r"\x09-\x0d\x20",
    "upper": "A-Z",
    "xdigit": "0-9A-Fa-f",
}


@dataclass(frozen=True)
class Claim:
    lane: str
    glob: str
    reason: str

    def render(self) -> dict[str, Any]:
        return asdict(self)


def _path_components(pattern: str) -> list[str]:
    parts: list[str] = []
    start = index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char == "[" and (bracket := _BRACKET.match(pattern, index)):
            index = bracket.end()
            continue
        if char == "/":
            parts.append(pattern[start:index])
            start = index + 1
        index += 1
    parts.append(pattern[start:])
    return parts


def normalize_glob(pattern: str) -> str:
    # Git ignores unescaped trailing spaces. Preserve an escaped space and the
    # leading slash on a single component, which makes it root-relative.
    pattern = _TRAILING_SPACES.sub(r"\1", pattern)
    pattern = re.sub(r"(?<!\\)((?:\\\\)*)\\/", r"\1/", pattern)
    if pattern.startswith("!") or any(ord(char) < 32 for char in pattern):
        raise LedgerError("invalid", "claim glob cannot be negated or contain control characters")
    parts = [part for part in _path_components(pattern) if part not in {"", "."}]
    if not parts or ".." in parts or any(_TRAILING_ESCAPE.search(part) for part in parts):
        raise LedgerError("invalid", "claim glob must be a valid repository path pattern")
    anchored = parts[0].startswith("!") or (len(parts) == 1 and "/" in pattern.rstrip("/"))
    root = "/" if anchored else ""
    directory = "/" if pattern.endswith("/") else ""
    if not directory:
        # Removing a final "." component can expose literal spaces at the end.
        parts[-1] = _TRAILING_SPACES.sub(lambda match: match[1] + r"\ " * len(match[2]), parts[-1])
    return root + "/".join(parts) + directory


def literal_prefix(pattern: str) -> str:
    """Represent complete path components with a trailing separator."""
    path = pattern.removeprefix("/").removesuffix("/")
    if not pattern.startswith("/") and "/" not in path:
        return ""
    prefix: list[str] = []
    start = 0
    for token in _GLOB_TOKEN.finditer(path):
        prefix.append(path[start : token.start()])
        if token.group(2):
            return "".join(prefix)
        prefix.append(token.group(1))
        start = token.end()
    prefix.append(path[start:])
    return "".join(prefix) + "/"


def _translate_bracket(match: re.Match[str]) -> str:
    negate = "^" if match["negate"] else ""
    parts: list[str] = []
    for token in _CLASS_TOKEN.finditer(match["contents"]):
        if token[1]:
            parts.append(re.escape(token[1]))
        elif token[2]:
            if token[2] not in _POSIX_CLASSES:
                raise LedgerError("invalid", "claim glob has an unknown bracket character class")
            parts.append(_POSIX_CLASSES[token[2]])
        else:
            parts.append("-" if token[3] == "-" else re.escape(token[3]))
    return f"(?!/)[{negate}{''.join(parts)}]"


def _translate_component(component: str) -> str:
    fragments: list[list[str]] = [[]]
    index = 0
    while index < len(component):
        char = component[index]
        parts = fragments[-1]
        if char == "\\":
            index += 1
            parts.append(re.escape(component[index]))
        elif char == "*":
            if parts or len(fragments) == 1:
                fragments.append([])
        elif char == "?":
            parts.append("[^/]")
        elif char == "[":
            bracket = _BRACKET.match(component, index)
            if bracket is None:
                raise LedgerError("invalid", "claim glob has an unterminated bracket class")
            parts.append(_translate_bracket(bracket))
            index = bracket.end() - 1
        else:
            parts.append(re.escape(char))
        index += 1
    if len(fragments) == 1:
        return "".join(fragments[0])
    # Like fnmatch, commit interior stars to the shortest match. Each matching
    # literal fragment has a fixed width and the next star can absorb any suffix.
    stars = [f"(?>[^/]*?{''.join(parts)})" for parts in fragments[1:-1]]
    return "".join([*fragments[0], *stars, "[^/]*", *fragments[-1]])


@dataclass(frozen=True)
class PathGlob:
    components: tuple[re.Pattern[bytes] | None, ...]
    directory_only: bool

    def matches(self, path: str) -> bool:
        parts = path.encode("utf-8", errors="surrogatepass").split(b"/")
        end = len(self.components)
        positions = {0}
        for depth, part in enumerate(parts, start=1):
            advanced: set[int] = set()
            for position in positions:
                component = self.components[position]
                if component is None:
                    # ** may consume this segment or leave it to the next component.
                    advanced.add(position)
                    position += 1
                    component = self.components[position]
                if component is not None and component.fullmatch(part):
                    advanced.add(position + 1)
            if end in advanced:
                if depth < len(parts) or not self.directory_only:
                    return True
                advanced.remove(end)
            if not advanced:
                return False
            positions = advanced
        return False


def compile_glob(pattern: str) -> PathGlob:
    """Compile a normalized gitignore-style glob for repository file paths."""
    path = pattern.removeprefix("/").removesuffix("/")
    components = _path_components(path)
    if not pattern.startswith("/") and len(components) == 1:
        components.insert(0, "**")
    matchers: list[re.Pattern[bytes] | None] = []
    try:
        for component in components:
            if len(component) >= 2 and set(component) == {"*"}:
                if not matchers or matchers[-1] is not None:
                    matchers.append(None)
            else:
                # Git wildmatch counts UTF-8 bytes, including inside character classes.
                raw_component = component.encode("utf-8").decode("latin-1")
                matchers.append(re.compile(_translate_component(raw_component).encode("latin-1")))
    except UnicodeEncodeError:
        raise LedgerError("invalid", "claim glob must be valid UTF-8") from None
    except re.PatternError:
        raise LedgerError("invalid", "claim glob has an invalid bracket range") from None
    if matchers[-1] is None:
        # A trailing /** must match at least one component after its parent.
        matchers.append(re.compile(rb"[^/]+"))
    return PathGlob(tuple(matchers), pattern.endswith("/"))


def _patterns(values: list[Any]) -> list[str]:
    if not values or not all(isinstance(value, str) for value in values):
        raise LedgerError("invalid", "globs must contain one or more path patterns")
    patterns = sorted({normalize_glob(value) for value in values})
    for pattern in patterns:
        compile_glob(pattern)
    return patterns


async def _editable_lane(uow: UnitOfWork, lane_id: str) -> None:
    lane = await uow.find_lane(lane_id)
    if lane is None:
        raise LedgerError("not_found", "lane not found")
    if lane.state == "archived":
        raise LedgerError("conflict", "lane is archived")


async def claim(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
    lane: str = args["lane"]
    await _editable_lane(uow, lane)
    reason: str = args["reason"]
    if not reason.strip():
        raise LedgerError("invalid", "claim reason must be non-empty")
    requested = [Claim(lane, pattern, reason) for pattern in _patterns(args["globs"])]
    existing = set(await uow.lane_claims(lane))
    changes = [entry for entry in requested if entry not in existing]
    if changes:
        await uow.save_claims(changes)
    return {"claims": [entry.render() for entry in requested], "changed": bool(changes)}


async def release(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
    lane: str = args["lane"]
    await _editable_lane(uow, lane)
    patterns = set(_patterns(args["globs"]))
    released = [entry for entry in await uow.lane_claims(lane) if entry.glob in patterns]
    if released:
        await uow.delete_claims(lane, [entry.glob for entry in released])
    return {"claims": [entry.render() for entry in released], "changed": bool(released)}


async def sync_items(uow: UnitOfWork, kind: str, desired: dict[str, dict[str, Any]]) -> None:
    """Refresh one state-backed item per subject, preserving unchanged occurrences."""
    active = await uow.active_item_subjects(kind)
    for subject in sorted(active - desired.keys()):
        await uow.clear_item(kind, subject)
    for subject, context in sorted(desired.items()):
        await uow.raise_item(kind, subject, "orchestrator", context)


def _shortest_prefixes(entries: list[tuple[Claim, str]]) -> list[str]:
    """Drop descendants so each candidate needs only two neighbor checks."""
    shortest: list[str] = []
    for prefix in sorted({prefix for _, prefix in entries}):
        if not shortest or not prefix.startswith(shortest[-1]):
            shortest.append(prefix)
    return shortest


def _comparable_prefix(prefix: str, ordered: list[str]) -> bool:
    index = bisect_left(ordered, prefix)
    return (index > 0 and prefix.startswith(ordered[index - 1])) or (
        index < len(ordered) and ordered[index].startswith(prefix)
    )


async def reconcile_claim_pairs(uow: UnitOfWork, claims: list[Claim]) -> None:
    by_lane: dict[str, list[tuple[Claim, str]]] = defaultdict(list)
    for entry in claims:
        by_lane[entry.lane].append((entry, literal_prefix(entry.glob)))
    indexes = {lane: _shortest_prefixes(entries) for lane, entries in by_lane.items()}
    desired: dict[str, dict[str, Any]] = {}
    for left, right in combinations(sorted(by_lane), 2):
        participants = [
            entry.render()
            for lane, other in ((left, right), (right, left))
            for entry, prefix in by_lane[lane]
            if _comparable_prefix(prefix, indexes[other])
        ]
        if participants:
            desired[json.dumps([left, right])] = {
                "lanes": [left, right],
                "prs": [],
                "claims": participants,
            }
    await sync_items(uow, CLAIM_OVERLAP, desired)
