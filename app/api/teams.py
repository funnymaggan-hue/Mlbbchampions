from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, rate_limit
from app.config import team_size
from app.db import get_session
from app.errors import err
from app.models import Team, Tournament, User
from app.services import teams as svc

router = APIRouter(prefix="/api")


class TeamIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=30)


class InviteIn(BaseModel):
    invitee_id: int = Field(gt=0)


class CaptainIn(BaseModel):
    user_id: int = Field(gt=0)


async def _public_tournament(session: AsyncSession, tid: int) -> Tournament:
    t = await session.get(Tournament, tid)
    if t is None or t.status == "draft":
        raise err(404, "not_found", "Turnir topilmadi.")
    return t


async def _view(session: AsyncSession, team: Team):
    t = await session.get(Tournament, team.tournament_id)
    return await svc.team_view(session, team, team_size(t.format), detailed=True)


# ---------- turnir ichidagi jamoalar ----------
@router.get("/tournaments/{tid}/teams")
async def list_teams(
    tid: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    t = await _public_tournament(session, tid)
    return await svc.public_team_list(session, t)


@router.get("/tournaments/{tid}/free-players")
async def free_players(
    tid: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    t = await _public_tournament(session, tid)
    return await svc.free_players(session, t, user)


@router.post(
    "/tournaments/{tid}/teams", dependencies=[Depends(rate_limit("team_create", 10, 60))]
)
async def create_team(
    tid: int,
    body: TeamIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    team = await svc.create_team(session, user, tid, body.name)
    view = await _view(session, team)
    await session.commit()
    return view


# ---------- mening jamoam va takliflarim ----------
@router.get("/my/teams")
async def my_teams(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    data = await svc.my_teams(session, user)
    await session.commit()
    return data


@router.get("/my/invitations")
async def my_invitations(
    user: User = Depends(current_user), session: AsyncSession = Depends(get_session)
):
    data = await svc.my_invitations(session, user)
    await session.commit()
    return data


# ---------- kapitan amallari ----------
@router.post(
    "/teams/{team_id}/invitations",
    dependencies=[Depends(rate_limit("invite", 20, 60))],
)
async def send_invite(
    team_id: int,
    body: InviteIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    inv = await svc.send_invite(session, user, team_id, body.invitee_id)
    await session.commit()
    return {
        "id": inv.id, "team_id": inv.team_id, "invitee_id": inv.invitee_id,
        "status": inv.status, "expires_at": svc._aware(inv.expires_at).isoformat(),
    }


@router.delete("/teams/{team_id}/invitations/{inv_id}")
async def cancel_invite(
    team_id: int,
    inv_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    inv = await svc.cancel_invite(session, user, inv_id)
    if inv.team_id != team_id:
        raise err(404, "not_found", "Taklif topilmadi.")
    await session.commit()
    return {"ok": True, "status": inv.status}


@router.post("/teams/{team_id}/captain")
async def set_captain(
    team_id: int,
    body: CaptainIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    team = await svc.set_captain(session, user, team_id, body.user_id)
    view = await _view(session, team)
    await session.commit()
    return view


@router.delete("/teams/{team_id}/members/{member_user_id}")
async def remove_member(
    team_id: int,
    member_user_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await svc.remove_member(session, user, team_id, member_user_id)
    await session.commit()
    return {"ok": True, "result": result}


@router.delete("/teams/{team_id}")
async def disband(
    team_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    await svc.disband_team(session, user, team_id)
    await session.commit()
    return {"ok": True}


# ---------- taklifni qabul qilish / rad etish ----------
@router.post(
    "/invitations/{inv_id}/accept",
    dependencies=[Depends(rate_limit("invite_resp", 20, 60))],
)
async def accept_invite(
    inv_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    inv = await svc.respond_invite(session, user, inv_id, accept=True)
    team = await session.get(Team, inv.team_id)
    view = await _view(session, team)
    await session.commit()
    return {"status": inv.status, "team": view}


@router.post(
    "/invitations/{inv_id}/decline",
    dependencies=[Depends(rate_limit("invite_resp", 20, 60))],
)
async def decline_invite(
    inv_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    inv = await svc.respond_invite(session, user, inv_id, accept=False)
    await session.commit()
    return {"status": inv.status}
