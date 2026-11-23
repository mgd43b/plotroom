"""Project-local deploy window commands."""

from typing import TYPE_CHECKING, Any

from plotroom.core.events import LedgerError

if TYPE_CHECKING:
    from plotroom.core.unit_of_work import UnitOfWork


async def freeze(uow: UnitOfWork, args: dict[str, Any]) -> dict[str, Any]:
    reason: str = args["reason"]
    if not reason.strip():
        raise LedgerError("invalid", "freeze reason must not be blank")
    await uow.set_deploy_freeze(reason)
    return {"frozen": True, "reason": reason}


async def unfreeze(uow: UnitOfWork, _args: dict[str, Any]) -> dict[str, Any]:
    await uow.set_deploy_freeze(None)
    return {"frozen": False, "reason": None}
