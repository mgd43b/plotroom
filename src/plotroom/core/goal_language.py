"""Parse and evaluate the operator's goal language over read-only issue facts."""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum, auto
from functools import cache
from typing import Literal

from plotroom.core.goal_projection import GoalFacts, GoalIssue, issue_number

_MAX_SOURCE = 4096
_MAX_TOKENS = 512
_MAX_DEPTH = 32
_WORD_OR_INTEGER = re.compile(r"[A-Za-z_][A-Za-z_0-9]*|[0-9]+")
_DECODER = json.JSONDecoder()


class GoalSyntaxError(ValueError):
    """A validation error at a zero-based character offset into the goal text."""

    def __init__(self, text: str, position: int, message: str) -> None:
        self.position = position
        self.line = text.count("\n", 0, position) + 1
        self.column = position - text.rfind("\n", 0, position)
        super().__init__(f"{message} at line {self.line}, column {self.column}")


@dataclass(frozen=True)
class _Span:
    start: int
    end: int


type _TokenKind = Literal["word", "integer", "string", "=", "(", ")", "end"]


@dataclass(frozen=True)
class _Token:
    kind: _TokenKind
    value: str
    span: _Span


def _scan(text: str) -> Iterator[_Token]:
    position = 0
    while position < len(text):
        char = text[position]
        if char.isspace():
            position += 1
            continue
        start = position
        if char == '"':
            try:
                value, position = _DECODER.raw_decode(text, position)
            except json.JSONDecodeError as exc:
                raise GoalSyntaxError(text, exc.pos, exc.msg) from exc
            assert isinstance(value, str)
            yield _Token("string", value, _Span(start, position))
        elif char in ("=", "(", ")"):
            position += 1
            yield _Token(char, char, _Span(start, position))
        elif match := _WORD_OR_INTEGER.match(text, position):
            position = match.end()
            kind = "integer" if char.isascii() and char.isdecimal() else "word"
            yield _Token(kind, match[0], _Span(start, position))
        else:
            raise GoalSyntaxError(text, position, f"Unexpected character {char!r}")
    yield _Token("end", "", _Span(position, position))


@dataclass(frozen=True)
class _Atom:
    span: _Span
    field: Literal["state", "milestone", "label", "assigned_to", "lane", "decision"]
    value: str | int | None


@dataclass(frozen=True)
class _Unary:
    span: _Span
    operator: Literal["not", "blocked_by", "group"]
    child: _Predicate


@dataclass(frozen=True)
class _Boolean:
    span: _Span
    operator: Literal["and", "or"]
    children: tuple[_Predicate, ...]


type _Predicate = _Atom | _Unary | _Boolean


@dataclass(frozen=True)
class Goal:
    text: str
    selector: _Predicate
    requirement: _Predicate


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens = _scan(text)
        self.count = 0
        self.current = next(self.tokens)

    def advance(self) -> _Token:
        token = self.current
        if self.count >= _MAX_TOKENS:
            raise GoalSyntaxError(self.text, token.span.start, "Goal has too many tokens")
        self.count += 1
        self.current = next(self.tokens)
        return token

    def expect(self, kind: _TokenKind, value: str | None = None) -> _Token:
        token = self.current
        if token.kind != kind or (value is not None and token.value != value):
            expected = repr(value) if value is not None else _TOKEN_NAMES[kind]
            raise GoalSyntaxError(self.text, token.span.start, f"Expected {expected}")
        return token if kind == "end" else self.advance()

    def parse(self) -> Goal:
        self.expect("word", "every")
        self.expect("word", "issue")
        self.expect("word", "where")
        selector = self.boolean("or", 0)
        self.expect("word", "satisfies")
        requirement = self.boolean("or", 0)
        self.expect("end")
        return Goal(self.text, selector, requirement)

    def boolean(self, operator: Literal["and", "or"], depth: int) -> _Predicate:
        def operand() -> _Predicate:
            return self.boolean("and", depth) if operator == "or" else self.term(depth)

        first = operand()
        children = [first]
        while self.current.kind == "word" and self.current.value == operator:
            self.advance()
            children.append(operand())
        if len(children) == 1:
            return first
        return _Boolean(_Span(first.span.start, children[-1].span.end), operator, tuple(children))

    def term(self, depth: int) -> _Predicate:
        token = self.current
        if depth >= _MAX_DEPTH:
            raise GoalSyntaxError(self.text, token.span.start, "Predicate nesting is too deep")
        if token.kind == "(":
            return self.group("group", token.span.start, depth)
        if token.kind != "word":
            raise GoalSyntaxError(self.text, token.span.start, "Expected a predicate")
        self.advance()
        field = token.value
        start = token.span.start
        if field == "not":
            child = self.term(depth + 1)
            return _Unary(_Span(start, child.span.end), "not", child)
        if field == "blocked_by":
            return self.group("blocked_by", start, depth)
        if field == "state":
            self.expect("=")
            state = self.expect("word")
            if state.value not in ("open", "closed"):
                raise GoalSyntaxError(self.text, state.span.start, "Expected 'open' or 'closed'")
            return _Atom(_Span(start, state.span.end), "state", state.value)
        if field == "milestone":
            self.expect("=")
            number = self.expect("integer")
            try:
                value = int(number.value)
            except ValueError:
                raise GoalSyntaxError(self.text, number.span.start, "Integer is too long") from None
            if value == 0:
                raise GoalSyntaxError(self.text, number.span.start, "Expected a positive integer")
            return _Atom(_Span(start, number.span.end), "milestone", value)
        if field in ("label", "lane", "decision"):
            name = self.expect("string")
            return _Atom(_Span(start, name.span.end), field, name.value)
        if field == "assigned_to":
            if self.current.kind == "word" and self.current.value == "human":
                human = self.advance()
                return _Atom(_Span(start, human.span.end), "assigned_to", None)
            login = self.expect("string")
            return _Atom(_Span(start, login.span.end), "assigned_to", login.value.casefold())
        raise GoalSyntaxError(self.text, start, f"Unknown predicate {field!r}")

    def group(self, operator: Literal["blocked_by", "group"], start: int, depth: int) -> _Unary:
        self.expect("(")
        child = self.boolean("or", depth + 1)
        end = self.expect(")").span.end
        return _Unary(_Span(start, end), operator, child)


_TOKEN_NAMES: dict[_TokenKind, str] = {
    "word": "a keyword",
    "integer": "a positive integer",
    "string": "a quoted string",
    "=": "'='",
    "(": "'('",
    ")": "')'",
    "end": "the end of the goal",
}


def parse_goal(text: str) -> Goal:
    """Validate a goal and retain its source for exact clause evidence."""
    if len(text) > _MAX_SOURCE:
        raise GoalSyntaxError(text, _MAX_SOURCE, "Goal text is too long")
    return _Parser(text).parse()


def uses_blockers(goal: Goal) -> bool:
    def contains_blocker(predicate: _Predicate) -> bool:
        if isinstance(predicate, _Unary):
            return predicate.operator == "blocked_by" or contains_blocker(predicate.child)
        if isinstance(predicate, _Boolean):
            return any(contains_blocker(child) for child in predicate.children)
        return False

    return contains_blocker(goal.selector) or contains_blocker(goal.requirement)


class _Truth(Enum):
    FALSE = auto()
    UNKNOWN = auto()
    TRUE = auto()

    @classmethod
    def of(cls, value: bool) -> _Truth:
        return cls.TRUE if value else cls.FALSE

    def negate(self) -> _Truth:
        return self if self is _Truth.UNKNOWN else self.of(self is _Truth.FALSE)


@dataclass(frozen=True)
class _ClauseFailure:
    span: _Span
    status: Literal["failed", "unavailable"]


@dataclass(frozen=True)
class _Result:
    truth: _Truth
    failures: tuple[_ClauseFailure, ...] = ()


def _result(truth: _Truth, span: _Span) -> _Result:
    if truth is _Truth.TRUE:
        return _Result(truth)
    status = "unavailable" if truth is _Truth.UNKNOWN else "failed"
    return _Result(truth, (_ClauseFailure(span, status),))


def _evaluate_atom(atom: _Atom, issue: GoalIssue, facts: GoalFacts) -> _Truth:
    field, value = atom.field, atom.value
    match field:
        case "state":
            return _Truth.of(issue.state == value)
        case "milestone":
            return _Truth.of(issue.milestone == value)
        case "label":
            return _Truth.of(value in issue.labels)
        case "lane":
            if value in issue.lanes:
                return _Truth.TRUE
            return _Truth.UNKNOWN if value in facts.unresolved_lanes else _Truth.FALSE
        case "decision":
            return _Truth.of(value in issue.decisions)
        case "assigned_to":
            login = facts.human_login if value is None else value
            if login is None:
                return _Truth.UNKNOWN
            assert isinstance(login, str)
            normalized = login.casefold()
            return _Truth.of(any(assignee.casefold() == normalized for assignee in issue.assignees))


@dataclass(frozen=True)
class GoalFailure:
    issue: int
    title: str
    clauses: tuple[GoalClause, ...]

    def render(self) -> dict[str, object]:
        return {
            "issue": self.issue,
            "title": self.title,
            "clauses": [
                {"clause": clause.text, "position": clause.position, "status": clause.status}
                for clause in self.clauses
            ],
        }


@dataclass(frozen=True)
class GoalClause:
    text: str
    position: int
    status: Literal["failed", "unavailable"]


def evaluate_goal(goal: Goal, facts: GoalFacts) -> list[GoalFailure]:
    """Return each selected violation, including evidence that leaves selection unknown."""

    def blocker_truth(issue: GoalIssue, predicate: _Predicate) -> _Truth:
        if issue.blockers is None:
            return _Truth.UNKNOWN
        truth = _Truth.FALSE
        for ref in issue.blockers:
            number = ref if isinstance(ref, int) else issue_number(facts.repository, ref)
            if number is None or (blocker := facts.issues.get(number)) is None:
                truth = _Truth.UNKNOWN
            elif blocker.state == "open":
                match = evaluate(predicate, number).truth
                if match is _Truth.TRUE:
                    return match
                if match is _Truth.UNKNOWN:
                    truth = match
        return truth

    @cache
    def evaluate(predicate: _Predicate, number: int) -> _Result:
        issue = facts.issues[number]
        if isinstance(predicate, _Atom):
            return _result(_evaluate_atom(predicate, issue, facts), predicate.span)
        if isinstance(predicate, _Unary):
            match predicate.operator:
                case "group":
                    return evaluate(predicate.child, number)
                case "not":
                    truth = evaluate(predicate.child, number).truth.negate()
                case "blocked_by":
                    truth = blocker_truth(issue, predicate.child)
            return _result(truth, predicate.span)
        results: list[_Result] = []
        for child in predicate.children:
            result = evaluate(child, number)
            if predicate.operator == "or" and result.truth is _Truth.TRUE:
                return result
            results.append(result)
        if predicate.operator == "and" and any(r.truth is _Truth.FALSE for r in results):
            truth = _Truth.FALSE
        elif any(r.truth is _Truth.UNKNOWN for r in results):
            truth = _Truth.UNKNOWN
        else:
            truth = _Truth.TRUE if predicate.operator == "and" else _Truth.FALSE
        return _Result(truth, tuple(clause for result in results for clause in result.failures))

    failures: list[GoalFailure] = []
    for number, issue in sorted(facts.issues.items()):
        selection = evaluate(goal.selector, number)
        if selection.truth is _Truth.FALSE:
            continue
        result = evaluate(goal.requirement, number) if selection.truth is _Truth.TRUE else selection
        if result.truth is _Truth.TRUE:
            continue
        clauses = tuple(
            GoalClause(
                goal.text[failure.span.start : failure.span.end], failure.span.start, failure.status
            )
            for failure in result.failures
        )
        failures.append(GoalFailure(number, issue.title, clauses))
    evaluate.cache_clear()
    return failures
