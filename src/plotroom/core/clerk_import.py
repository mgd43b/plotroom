"""Import typed clerk rows through existing lane, trigger, and checklist rules."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypedDict, cast

from plotroom.core.events import FieldSpec, LedgerError, NewEvent, validate_fields
from plotroom.core.lanes import checklist_import, start_lane
from plotroom.core.merge_lane import lane_matches_branch
from plotroom.core.next_items import ItemKind
from plotroom.core.trigger_actions import NOTIFY, SEND, DeliveryRoute
from plotroom.core.triggers import LeafRegistry, create_trigger
from plotroom.core.work_items import refresh_idle_workers

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork

CLERK_IMPORT_ENTRY = "core.clerk_import_entry"
CLERK_IMPORT_ENTRY_FIELDS = (FieldSpec("category", "str"), FieldSpec("id", "str"))

BIND_TRIGGER = ItemKind("clerk_import_bind_trigger", 20, False, ("Bind the legacy trigger",))
REVIEW_PULL = ItemKind("clerk_import_review_pull", 20, True, ("Review the legacy PR lane",))
SKIPPED_PULL = ItemKind("clerk_import_skipped_pull", 20, True, ("Review the legacy PR hold",))
CLERK_IMPORT_ITEMS = (BIND_TRIGGER, REVIEW_PULL, SKIPPED_PULL)

_PLACEHOLDER_NOTE = "Imported fire-time fields such as [sha8] and [N] are literal text."
_HOLD_STATE = re.compile(
    r"(?:on[ \t]+hold|hold(?:ing)?|held)(?:$|[ \t]+(?:for|by|until|pending)\b|[ \t]*[:(—])",
    re.IGNORECASE,
)
_TRIGGER_REF = re.compile(r"(?:Trigger[ \t]+)?(?:(T[0-9]+)|`(T[0-9]+)`)", re.IGNORECASE)


class TriggerImportRow(TypedDict):
    label: str
    source: str
    predicate: dict[str, Any] | None
    text: str | None
    channel: str | None
    notify: bool
    fired: bool


class PullImportRow(TypedDict):
    pr: int
    worker: str
    channel: str
    state: str
    after_merge: str


class SkipImportRow(TypedDict):
    pr: int
    reason: str


_TRIGGER_FIELDS = (
    FieldSpec("label", "str"),
    FieldSpec("source", "str"),
    FieldSpec("predicate", "object", required=False),
    FieldSpec("text", "str", required=False),
    FieldSpec("channel", "str", required=False),
    FieldSpec("notify", "bool"),
    FieldSpec("fired", "bool"),
)
_PULL_FIELDS = (
    FieldSpec("pr", "int"),
    FieldSpec("worker", "str"),
    FieldSpec("channel", "str"),
    FieldSpec("state", "str"),
    FieldSpec("after_merge", "str"),
)
_SKIP_FIELDS = (FieldSpec("pr", "int"), FieldSpec("reason", "str"))
_NULLABLE_TRIGGER_FIELDS = frozenset({"predicate", "text", "channel"})


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _trigger_id(label: str) -> str:
    return f"trigger_clerk_{_digest(label)}"


def _label_source_receipt(label: str) -> str:
    return f"trigger-source:{_digest(label)}"


def _ambiguous_label_receipt(label: str) -> str:
    return f"ambiguous-trigger:{_digest(label)}"


def _exact_trigger_label(text: str) -> str | None:
    # A checklist sentence mentioning a label is still a checklist sentence.
    text = text.strip()
    if text.startswith("`") and text.endswith("`"):
        text = text[1:-1]
    match = _TRIGGER_REF.fullmatch(text)
    return (match[1] or match[2]).upper() if match else None


def _validated_rows(
    values: list[Any],
    fields: tuple[FieldSpec, ...],
    key: str,
    what: str,
    *,
    nullable: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    rows: dict[str | int, dict[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            raise LedgerError("invalid", f"{what}: each row must be an object")
        row = cast("dict[str, Any]", value)
        validate_fields(
            fields,
            {name: data for name, data in row.items() if data is not None or name not in nullable},
            what,
        )
        row = {**dict.fromkeys(nullable), **row}
        for field in fields:
            if isinstance(text := row[field.name], str):
                try:
                    text.encode("utf-8")
                except UnicodeError:
                    raise LedgerError("invalid", f"{what}: text must be valid UTF-8") from None
        identity = cast("str | int", row[key])
        if key == "pr" and not 0 < cast(int, identity) <= 2**63 - 1:
            raise LedgerError("invalid", f"{what}: PR must be a positive SQLite integer")
        if key == "label" and not cast(str, identity).strip():
            raise LedgerError("invalid", f"{what}: trigger label must not be blank")
        if identity in rows and rows[identity] != row:
            raise LedgerError("invalid", f"{what}: conflicting rows for {identity!r}")
        rows[identity] = row
    return list(rows.values())


def _target_worker(channel: str, routes: Mapping[str, DeliveryRoute]) -> str | None:
    candidates = [
        worker
        for worker, route in routes.items()
        if channel == worker
        or (route.channel == "switchboard" and channel == f"relay:{route.destination}")
        or (
            route.channel == "claude"
            and channel in (route.destination, f"session-message:{route.destination}")
        )
    ]
    return candidates[0] if len(candidates) == 1 else None


async def _bind(uow: UnitOfWork, row: TriggerImportRow, reason: str) -> tuple[str, str]:
    item = await uow.raise_item(
        BIND_TRIGGER.name,
        f"{row['label']}:{_digest(row['source'])}",
        "orchestrator",
        {"label": row["label"], "source": row["source"], "reason": reason},
    )
    return "items", item.id


async def _import_trigger(
    uow: UnitOfWork,
    row: TriggerImportRow,
    digest: str,
    *,
    repository: str | None,
    registry: LeafRegistry,
    routes: Mapping[str, DeliveryRoute],
) -> tuple[str, str]:
    predicate, text, channel = row["predicate"], row["text"], row["channel"]
    label = row["label"]
    source_key = _label_source_receipt(label)
    source = await uow.find_event("core", CLERK_IMPORT_ENTRY, source_key)
    if source is None:
        await uow.append(
            NewEvent(
                "core", CLERK_IMPORT_ENTRY, source_key, {"category": "trigger_source", "id": digest}
            )
        )
    elif source.fields["id"] != digest:
        category, item_id = await _bind(
            uow, row, "An imported trigger entry already has this label"
        )
        await uow.append(
            NewEvent(
                "core",
                CLERK_IMPORT_ENTRY,
                _ambiguous_label_receipt(label),
                {"category": category, "id": item_id},
            )
        )
        return category, item_id
    if predicate is None or set(predicate) != {"pr_merged"} or not text or not text.strip():
        return await _bind(uow, row, "The trigger needs an exact condition and recognized message")
    if channel is not None:
        worker = _target_worker(channel, routes)
        if worker is None:
            return await _bind(uow, row, "The channel does not match a unique configured worker")
        action = {SEND: {"worker": worker, "text": text}}
    elif row["notify"]:
        action = {NOTIFY: {"text": text}}
    else:
        return await _bind(uow, row, "The trigger does not name a supported recipient")
    try:
        result = await create_trigger(
            uow,
            {"predicate": predicate, "action": action, "imported_fired": row["fired"]},
            registry=registry,
            repository=repository,
            delivery_routes=routes,
            imported_id=_trigger_id(label),
            import_note=_PLACEHOLDER_NOTE if "[sha8]" in text or "[N]" in text else None,
            notify_on_fire=channel is not None and row["notify"],
        )
    except LedgerError as exc:
        if exc.code != "invalid":
            raise
        return await _bind(uow, row, exc.message)
    return "triggers", result["id"]


async def _review_pull(uow: UnitOfWork, row: PullImportRow, reason: str) -> tuple[str, str]:
    context = dict(row)
    context.pop("state")
    item = await uow.raise_item(
        REVIEW_PULL.name, str(row["pr"]), "orchestrator", {**context, "reason": reason}
    )
    return "items", item.id


async def _import_pull(
    uow: UnitOfWork, row: PullImportRow, *, repository: str | None, workers: frozenset[str]
) -> tuple[str, str]:
    worker, number = row["worker"], row["pr"]
    if worker not in workers:
        return await _review_pull(uow, row, "The PR owner is not a configured worker")
    if repository is None:
        return await _review_pull(uow, row, "No GitHub projection is configured")
    pull = await uow.pull_request(repository, number)
    if (
        pull is None
        or not pull.complete
        or pull.state != "open"
        or pull.head_repository != repository
        or not pull.branch.strip()
        or not pull.head_sha.strip()
        or pull != await uow.open_branch_pull(repository, pull.branch)
    ):
        return await _review_pull(uow, row, "The PR has no unambiguous open projected head branch")
    lane = f"clerk-pr-{number}"
    if (
        await uow.find_lane(lane) is not None
        or await uow.worker_lane(worker) is not None
        or any(
            active.branch == pull.branch or lane_matches_branch(active, pull.branch)
            for active in await uow.active_lanes()
        )
    ):
        return await _review_pull(uow, row, "The worker, branch, or PR already has a lane")
    after_merge = row["after_merge"]
    label = _exact_trigger_label(after_merge)
    trigger = None
    if label is not None and not await uow.find_event(
        "core", CLERK_IMPORT_ENTRY, _ambiguous_label_receipt(label)
    ):
        trigger = await uow.find_trigger(_trigger_id(label))
    await start_lane(
        uow,
        {"lane": lane, "worker": worker, "branch": pull.branch, "work_items": []},
        workers=workers,
        imported_channel=row["channel"],
        imported_trigger_id=trigger.id if trigger is not None else None,
        refresh_idle=False,
    )
    if trigger is None and after_merge.strip():
        await checklist_import(
            uow,
            {
                "lane": lane,
                "rows": [
                    {
                        "step": after_merge,
                        "owner": worker,
                        "ref": f"clerk-ledger:#{number}",
                    }
                ],
            },
            repository=repository,
        )
    return "lanes", lane


async def _import_skip(uow: UnitOfWork, row: SkipImportRow) -> tuple[str, str]:
    item = await uow.raise_item(SKIPPED_PULL.name, str(row["pr"]), "orchestrator", dict(row))
    return "items", item.id


async def clerk_import(
    uow: UnitOfWork,
    args: dict[str, Any],
    *,
    repository: str | None,
    workers: frozenset[str],
    registry: LeafRegistry,
    delivery_routes: Mapping[str, DeliveryRoute],
) -> dict[str, Any]:
    """Apply the import and journal its rows atomically within the writer command."""
    declared = args.get("ledger_repository")
    if declared is None and (args["pulls"] or args["skips"]):
        raise LedgerError("invalid", "a clerk ledger repository is required")
    if declared is not None and declared.lower() != repository:
        raise LedgerError("conflict", "the clerk ledger repository does not match this project")
    triggers = _validated_rows(
        args["triggers"],
        _TRIGGER_FIELDS,
        "label",
        "clerk trigger",
        nullable=_NULLABLE_TRIGGER_FIELDS,
    )
    pulls = _validated_rows(args["pulls"], _PULL_FIELDS, "pr", "clerk PR")
    skips = _validated_rows(args["skips"], _SKIP_FIELDS, "pr", "clerk skip")
    explicit_holds = {row["pr"] for row in skips}
    skips.extend(
        {"pr": row["pr"], "reason": row["state"]}
        for row in pulls
        if row["pr"] not in explicit_holds and _HOLD_STATE.match(row["state"].strip())
    )
    result: dict[str, dict[str, str]] = {"triggers": {}, "lanes": {}, "items": {}}
    added_lane = False
    for kind, rows, key in (
        ("trigger", triggers, "label"),
        ("pull", pulls, "pr"),
        ("skip", skips, "pr"),
    ):
        for row in rows:
            source_id = str(row[key])
            # State changes affect hold advisories independently of lane identity.
            stable = {name: value for name, value in row.items() if name != "state"}
            digest = _digest(json.dumps(stable, sort_keys=True, separators=(",", ":")))
            receipt_id = f"{kind}:{digest}"
            receipt = await uow.find_event("core", CLERK_IMPORT_ENTRY, receipt_id)
            if receipt is not None:
                category, identifier = receipt.fields["category"], receipt.fields["id"]
            else:
                if kind == "trigger":
                    category, identifier = await _import_trigger(
                        uow,
                        cast(TriggerImportRow, row),
                        digest,
                        repository=repository,
                        registry=registry,
                        routes=delivery_routes,
                    )
                elif kind == "pull":
                    category, identifier = await _import_pull(
                        uow, cast(PullImportRow, row), repository=repository, workers=workers
                    )
                    added_lane = added_lane or category == "lanes"
                else:
                    category, identifier = await _import_skip(uow, cast(SkipImportRow, row))
                await uow.append(
                    NewEvent(
                        "core",
                        CLERK_IMPORT_ENTRY,
                        receipt_id,
                        {"category": category, "id": identifier},
                        lane_id=identifier if category == "lanes" else None,
                    )
                )
            result_key = f"{kind}:{source_id}" if category == "items" else source_id
            result[category][result_key] = identifier
    if added_lane:
        await refresh_idle_workers(uow, workers)
    return result
