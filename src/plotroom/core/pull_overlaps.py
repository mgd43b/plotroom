"""Reconcile conflicts against the confirmed file lists of open pull requests."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from plotroom.core.claims import (
    PR_CLAIM_OVERLAP,
    TICKET_TREE_MOVE,
    Claim,
    compile_glob,
    sync_items,
)
from plotroom.core.github_projection import FILES

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork


@dataclass(frozen=True)
class _PullFiles:
    number: int
    lane: str | None
    paths: set[str]
    moves: set[str]
    edits: set[str]


async def _confirmed_files(
    uow: UnitOfWork, repository: str, tree: PurePosixPath | None
) -> list[_PullFiles]:
    confirmed: list[_PullFiles] = []
    for pull in await uow.open_pulls(repository):
        resource = await uow.github_resource(repository, FILES, str(pull.number))
        # An unsuccessful refresh may leave a confirmed file list cached.
        if resource is None or resource.attributes.get("head_sha") != pull.head_sha:
            continue
        lane = await uow.branch_lane(pull.branch) if pull.head_repository == repository else None
        paths: set[str] = set()
        moves: set[str] = set()
        edits: set[str] = set()
        for row in resource.records:
            if row.fields["head_sha"] != pull.head_sha:
                continue
            path: str = row.fields["path"]
            paths.add(path)
            prior: str | None = row.fields.get("previous_path")
            if row.fields["status"] != "renamed" or prior is None:
                edits.add(path)
                continue
            paths.add(prior)
            if tree is not None and (
                PurePosixPath(prior).is_relative_to(tree)
                or PurePosixPath(path).is_relative_to(tree)
            ):
                moves.update((prior, path))
        confirmed.append(
            _PullFiles(pull.number, None if lane is None else lane.id, paths, moves, edits)
        )
    return confirmed


async def _reconcile_pr_claims(
    uow: UnitOfWork, pulls: list[_PullFiles], claims: list[Claim]
) -> None:
    matchers = [(claim, compile_glob(claim.glob)) for claim in claims]
    desired: dict[str, dict[str, object]] = {}
    for pull in pulls:
        matches: dict[str, list[dict[str, object]]] = defaultdict(list)
        for claim, matcher in matchers:
            if pull.lane == claim.lane:
                continue
            paths = sorted(path for path in pull.paths if matcher.matches(path))
            if paths:
                matches[claim.lane].append({"claim": claim.render(), "paths": paths})
        for lane, evidence in matches.items():
            desired[json.dumps([pull.number, lane])] = {
                "lanes": [lane] if pull.lane is None else sorted((lane, pull.lane)),
                "prs": [pull.number],
                "claiming_lane": lane,
                "matches": evidence,
            }
    await sync_items(uow, PR_CLAIM_OVERLAP, desired)


def _merge_order(mover: int, dependencies: dict[int, set[int]]) -> list[int]:
    pending = [mover]
    visited: set[int] = set()
    while pending:
        pr = pending.pop()
        if pr not in visited:
            visited.add(pr)
            pending.extend(dependencies.get(pr, ()))
    graph = {pr: sorted(dependencies.get(pr, ())) for pr in sorted(visited)}
    try:
        return list(TopologicalSorter(graph).static_order())
    except CycleError:
        return []


async def _reconcile_moves(uow: UnitOfWork, pulls: list[_PullFiles]) -> None:
    by_number = {pull.number: pull for pull in pulls}
    by_path: dict[str, set[int]] = defaultdict(set)
    for pull in pulls:
        for path in pull.edits:
            by_path[path].add(pull.number)
    conflicts: dict[int, dict[str, list[int]]] = {}
    for pull in pulls:
        edits = {
            path: sorted(by_path[path] - {pull.number})
            for path in sorted(pull.moves)
            if by_path[path] - {pull.number}
        }
        if edits:
            conflicts[pull.number] = edits
    dependencies = {
        mover: {number for numbers in edits.values() for number in numbers}
        for mover, edits in conflicts.items()
    }
    desired: dict[str, dict[str, object]] = {}
    for mover, edits in conflicts.items():
        editors = sorted(dependencies[mover])
        prs = [*editors, mover]
        order = _merge_order(mover, dependencies)
        context: dict[str, object] = {
            "lanes": sorted(
                {lane for number in prs if (lane := by_number[number].lane) is not None}
            ),
            "prs": sorted(prs),
            "moving_pr": mover,
            "editing_prs": editors,
            "edits": edits,
            "suggested_order": order,
        }
        if not order:
            context["coordination_required"] = True
        desired[str(mover)] = context
    await sync_items(uow, TICKET_TREE_MOVE, desired)


async def reconcile_pull_overlaps(
    uow: UnitOfWork, repository: str, tree_path: str | None, claims: list[Claim]
) -> None:
    tree = PurePosixPath(tree_path) if tree_path is not None else None
    pulls = await _confirmed_files(uow, repository, tree) if claims or tree is not None else []
    await _reconcile_pr_claims(uow, pulls, claims)
    await _reconcile_moves(uow, pulls)
