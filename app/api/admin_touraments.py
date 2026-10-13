from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import (
    BaseModel, Field, field_validator, model_validator,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.api.public import _counts, tournament_out
from app.config import FORMATS, team_size
from app.db import get_session
from app.errors import err
from app.models import Registration, Team, Tournament, User
from app.services import teams as team_svc
from app.services.audit import write_audit, write_audit_isolated

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])

STRUCTURAL = {"format", "stage_type", "max_participants", "pairing_method"}
STRUCT_EDITABLE = {"draft", "reg_open", "reg_closed"}
NULLABLE = {"banner_url", "reg_start", "reg_end", "starts_at"}

# "paired" qo'lda o'rnatilmaydi: juftliklar tasdiqlanganda avtomatik (4-bosqich)
TRANSITIONS = {
    "draft": {"reg_open", "cancelled"},
    "reg_open": {"reg_closed", "cancelled"},
    "reg_closed": {"reg_open", "cancelled"},
    "paired": {"running", "cancelled"},
    "running": {"finished", "cancelled"},
    "finished": set(),
    "cancelled": set(),
}


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _check_dates(rs, re_, st) -> None:
    rs, re_, st = _utc(rs), _utc(re_), _utc(st)
    if rs and re_ and rs >= re_:
        raise err(422, "bad_dates", "Ro'yxat boshlanishi tugashidan oldin bo'lishi kerak.")
    if re_ and st and st < re_:
        raise err(422, "bad_dates", "Turnir ro'yxat tugashidan oldin boshlanmasligi kerak.")


class TournamentIn(BaseModel):
    name: str = Field(min_length=3, max_length=150)
    description: str = Field(default="", max_length=4000)
    banner_url: str | None = Field(default=None, pattern=r"^https://\S+$", max_length=500)
    format: str
    stage_type: Literal["single_elim", "group"] = "single_elim"
    max_participants: int = Field(default=16, ge=2, le=512)
    reg_start: datetime | None = None
    reg_end: datetime | None = None
    starts_at: datetime | None = None
    prizes: str = Field(default="", max_length=4000)
    rules: str = Field(default="", max_length=8000)
    pairing_method: Literal["manual", "random"] = "random"
    result_policy: Literal["captain_then_admin", "admin_only"] = "captain_then_admin"

    @field_validator("format")
    @classmethod
    def _fmt(cls, v):
        if v not in FORMATS:
            raise ValueError(f"Format: {', '.join(FORMATS)}")
        return v

    @field_validator("reg_start", "reg_end", "starts_at")
    @classmethod
    def _tz(cls, v):
        return _utc(v)

    @model_validator(mode="after")
    def _dates(self):
        if self.reg_start and self.reg_end and self.reg_start >= self.reg_end:
            raise ValueError("Ro'yxat boshlanishi tugashidan oldin bo'lishi kerak")
        if self.reg_end and self.starts_at and self.starts_at < self.reg_end:
            raise ValueError("Turnir ro'yxat tugashidan oldin boshlanmasligi kerak")
        return self


class TournamentPatch(BaseModel):
    version: int | None = None
    name: str | None = Field(default=None, min_length=3, max_length=150)
    description: str | None = Field(default=None, max_length=4000)
    banner_url: str | None = Field(default=None, pattern=r"^https://\S+$", max_length=500)
    format: str | None = None
    stage_type: Literal["single_elim", "group"] | None = None
    max_participants: int | None = Field(default=None, ge=2, le=512)
    reg_start: datetime | None = None
    reg_end: datetime | None = None
    starts_at: datetime | None = None
    prizes: str | None = Field(default=None, max_length=4000)
    rules: str | None = Field(default=None, max_length=8000)
    pairing_method: Literal["manual", "random"] | None = None
    result_policy: Literal["captain_then_admin", "admin_only"] | None = None

    @field_validator("format")
    @classmethod
    def _fmt(cls, v):
        if v is not None and v not in FORMATS:
            raise ValueError(f"Format: {', '.join(FORMATS)}")
        return v

    @field_validator("reg_start", "reg_end", "starts_at")
    @classmethod
    def _tz(cls, v):
        return _utc(v)


class StatusIn(BaseModel):
    status: Literal[
        "draft", "reg_open", "reg_closed", "paired", "running", "finished", "cancelled"
    ]
    version: int | None = None


class UserRef(BaseModel):
    user_id: int = Field(gt=0)


async def audited(session: AsyncSession, admin: User, action, otype, oid, details, fn):
    """Amalni bajaradi; muvaffaqiyatda audit amal bilan birga commit bo'ladi,
    xatoda alohida 'fail' yozuvi saqlanadi."""
    try:
        result = await fn()
    except HTTPException:
        await session.rollback()
        await write_audit_isolated(admin.telegram_id, action, otype, oid, details, "fail")
        raise
    await write_audit(session, admin.telegram_id, action, otype, oid, details)
    await session.commit()
    return result


async def _lock(session: AsyncSession, tid: int) -> Tournament:
    t = await session.scalar(
        select(Tournament).where(Tournament.id == tid).with_for_update()
    )
    if t is None:
        raise err(404, "not_found", "Turnir topilmadi.")
    return t


def admin_out(t: Tournament, counts: dict | None = None) -> dict:
    d = tournament_out(t, **(counts or {"registered": 0, "waitlist": 0}))
    d.update(
        version=t.version, pairing_method=t.pairing_method,
        result_policy=t.result_policy,
    )
    return d


# ---------- turnirlar ----------
@router.get("/tournaments")
async def list_all(session: AsyncSession = Depends(get_session)):
    ts = list(await session.scalars(select(Tournament).order_by(Tournament.id.desc())))
    counts = await _counts(session, [t.id for t in ts])
    return [admin_out(t, counts[t.id]) for t in ts]


@router.get("/tournaments/{tid}")
async def get_one(tid: int, session: AsyncSession = Depends(get_session)):
    t = await session.get(Tournament, tid)
    if t is None:
        raise err(404, "not_found", "Turnir topilmadi.")
    counts = await _counts(session, [t.id])
    return admin_out(t, counts[t.id])


@router.post("/tournaments")
async def create_tournament(
    body: TournamentIn,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    t = Tournament(**body.model_dump(), status="draft")
    session.add(t)
    await session.flush()
    await write_audit(
        session, admin.telegram_id, "tournament.create", "tournament", t.id,
        body.model_dump(mode="json"),
    )
    await session.commit()
    return admin_out(t)


@router.patch("/tournaments/{tid}")
async def update_tournament(
    tid: int,
    body: TournamentPatch,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    raw = body.model_dump(exclude_unset=True)
    version = raw.pop("version", None)
    changes = {k: v for k, v in raw.items() if v is not None or k in NULLABLE}
    details = {
        k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in changes.items()
    }

    async def fn():
        t = await _lock(session, tid)
        if version is not None and version != t.version:
            raise err(
                409, "version_conflict",
                "Turnirni boshqa admin o'zgartirgan. Sahifani yangilab qayta urining.",
            )
        if not changes:
            raise err(400, "no_changes", "O'zgartirish yo'q.")
        if STRUCTURAL & set(changes) and t.status not in STRUCT_EDITABLE:
            raise err(
                409, "locked",
                "Turnir boshlangan: format, bosqich turi, limit va juftlash usulini "
                "o'zgartirib bo'lmaydi.",
            )
        if "format" in changes and changes["format"] != t.format:
            has_teams = await session.scalar(
                select(func.count()).select_from(Team).where(Team.tournament_id == t.id)
            )
            if has_teams:
                raise err(
                    409, "has_teams",
                    "Jamoalar mavjud: formatni o'zgartirib bo'lmaydi.",
                )
        if "max_participants" in changes:
            registered = await session.scalar(
                select(func.count()).select_from(Registration).where(
                    Registration.tournament_id == t.id,
                    Registration.status == "registered",
                )
            )
            if changes["max_participants"] < registered:
                raise err(
                    409, "limit_too_low",
                    f"Limit ro'yxatdan o'tganlar sonidan ({registered}) kam bo'lmasin.",
                )
        _check_dates(
            changes.get("reg_start", t.reg_start),
            changes.get("reg_end", t.reg_end),
            changes.get("starts_at", t.starts_at),
        )
        for k, v in changes.items():
            setattr(t, k, v)
        t.version += 1
        await session.flush()
        return t

    t = await audited(session, admin, "tournament.update", "tournament", tid, details, fn)
    counts = await _counts(session, [t.id])
    return admin_out(t, counts[t.id])


@router.post("/tournaments/{tid}/status")
async def change_status(
    tid: int,
    body: StatusIn,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    async def fn():
        t = await _lock(session, tid)
        if body.version is not None and body.version != t.version:
            raise err(409, "version_conflict", "Turnirni boshqa admin o'zgartirgan.")
        if body.status == "paired":
            raise err(
                409, "bad_transition",
                "'Juftliklar belgilangan' holati juftliklar tasdiqlanganda avtomatik o'rnatiladi.",
            )
        if body.status not in TRANSITIONS[t.status]:
            raise err(
                409, "bad_transition",
                f"'{t.status}' holatidan '{body.status}' ga o'tib bo'lmaydi.",
            )
        details["from"] = t.status
        t.status = body.status
        t.version += 1
        await session.flush()
        return t

    details = {"to": body.status}
    t = await audited(session, admin, "tournament.status", "tournament", tid, details, fn)
    counts = await _counts(session, [t.id])
    return admin_out(t, counts[t.id])


# ---------- jamoalarni boshqarish (admin) ----------
@router.get("/tournaments/{tid}/teams")
async def admin_list_teams(tid: int, session: AsyncSession = Depends(get_session)):
    t = await session.get(Tournament, tid)
    if t is None:
        raise err(404, "not_found", "Turnir topilmadi.")
    teams = list(
        await session.scalars(
            select(Team).where(Team.tournament_id == tid).order_by(Team.id)
        )
    )
    size = team_size(t.format)
    return [await team_svc.team_view(session, x, size, detailed=True) for x in teams]


@router.post("/teams/{team_id}/members")
async def admin_add_member(
    team_id: int,
    body: UserRef,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    async def fn():
        team = await team_svc.admin_add_member(session, team_id, body.user_id)
        t = await session.get(Tournament, team.tournament_id)
        return await team_svc.team_view(session, team, team_size(t.format), True)

    return await audited(
        session, admin, "team.add_member", "team", team_id, {"user_id": body.user_id}, fn
    )


@router.delete("/teams/{team_id}/members/{user_id}")
async def admin_remove_member(
    team_id: int,
    user_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    async def fn():
        return await team_svc.remove_member(
            session, admin, team_id, user_id, is_admin=True
        )

    result = await audited(
        session, admin, "team.remove_member", "team", team_id, {"user_id": user_id}, fn
    )
    return {"ok": True, "result": result}


@router.post("/teams/{team_id}/captain")
async def admin_set_captain(
    team_id: int,
    body: UserRef,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    async def fn():
        team = await team_svc.set_captain(
            session, admin, team_id, body.user_id, is_admin=True
        )
        t = await session.get(Tournament, team.tournament_id)
        return await team_svc.team_view(session, team, team_size(t.format), True)

    return await audited(
        session, admin, "team.set_captain", "team", team_id, {"user_id": body.user_id}, fn
    )


@router.delete("/teams/{team_id}")
async def admin_disband(
    team_id: int,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    async def fn():
        await team_svc.disband_team(session, admin, team_id, is_admin=True)

    await audited(session, admin, "team.disband", "team", team_id, None, fn)
    return {"ok": True}
