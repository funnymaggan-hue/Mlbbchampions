from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import team_size
from app.errors import err
from app.models import (
    PlayerProfile, Registration, Team, TeamInvitation, TeamMember,
    Tournament, User,
)

ROSTER_OPEN = ("reg_open", "reg_closed")
INVITE_TTL = timedelta(hours=48)

_STATUS_UZ = {
    "accepted": "qabul qilingan",
    "declined": "rad etilgan",
    "cancelled": "bekor qilingan",
    "expired": "muddati tugagan",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ---------- yordamchilar ----------
async def _lock_tournament(session: AsyncSession, tid: int) -> Tournament:
    t = await session.scalar(
        select(Tournament).where(Tournament.id == tid).with_for_update()
    )
    if t is None or t.status == "draft":
        raise err(404, "not_found", "Turnir topilmadi.")
    return t


async def _roster_tournament(session: AsyncSession, tid: int) -> Tournament:
    t = await _lock_tournament(session, tid)
    if t.status not in ROSTER_OPEN:
        raise err(409, "roster_locked", "Hozir jamoa tarkibini o'zgartirib bo'lmaydi.")
    return t


async def _load_team(session: AsyncSession, team_id: int) -> Team:
    team = await session.get(Team, team_id)
    if team is None:
        raise err(404, "not_found", "Jamoa topilmadi.")
    return team


async def _team_and_tournament(session: AsyncSession, team_id: int):
    team = await _load_team(session, team_id)
    t = await _roster_tournament(session, team.tournament_id)
    await session.refresh(team)
    return team, t


async def _member_count(session: AsyncSession, team_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team_id)
    )


async def _sync_status(session: AsyncSession, team: Team, size: int) -> None:
    n = await _member_count(session, team.id)
    team.status = "full" if n >= size else "forming"
    await session.flush()


async def _is_registered(session: AsyncSession, tid: int, user_id: int) -> bool:
    return (
        await session.scalar(
            select(Registration.id).where(
                Registration.tournament_id == tid,
                Registration.user_id == user_id,
                Registration.status == "registered",
            )
        )
    ) is not None


async def _membership(session: AsyncSession, tid: int, user_id: int):
    return await session.scalar(
        select(TeamMember).where(
            TeamMember.tournament_id == tid, TeamMember.user_id == user_id
        )
    )


async def expire_stale(session: AsyncSession) -> None:
    await session.execute(
        update(TeamInvitation)
        .where(TeamInvitation.status == "pending", TeamInvitation.expires_at < _now())
        .values(status="expired")
    )


async def _cancel_user_invites(session: AsyncSession, tid: int, user_id: int) -> None:
    team_ids = select(Team.id).where(Team.tournament_id == tid)
    await session.execute(
        update(TeamInvitation)
        .where(
            TeamInvitation.invitee_id == user_id,
            TeamInvitation.status == "pending",
            TeamInvitation.team_id.in_(team_ids),
        )
        .values(status="cancelled")
    )


async def _cancel_team_invites(session: AsyncSession, team_id: int) -> None:
    await session.execute(
        update(TeamInvitation)
        .where(TeamInvitation.team_id == team_id, TeamInvitation.status == "pending")
        .values(status="cancelled")
    )


async def _delete_team(session: AsyncSession, team_id: int) -> None:
    await session.execute(delete(TeamInvitation).where(TeamInvitation.team_id == team_id))
    await session.execute(delete(TeamMember).where(TeamMember.team_id == team_id))
    await session.execute(delete(Team).where(Team.id == team_id))


# ---------- amallar ----------
async def create_team(session: AsyncSession, user: User, tid: int, name: str) -> Team:
    name = name.strip()
    t = await _roster_tournament(session, tid)
    if not await _is_registered(session, t.id, user.id):
        raise err(
            403, "not_registered",
            "Jamoa yaratish uchun turnirda ro'yxatdan o'tgan bo'lishingiz kerak "
            "(navbat ro'yxati yetarli emas).",
        )
    if await _membership(session, t.id, user.id):
        raise err(409, "already_in_team", "Siz allaqachon jamoadasiz.")
    taken = await session.scalar(
        select(Team.id).where(
            Team.tournament_id == t.id, func.lower(Team.name) == name.lower()
        )
    )
    if taken:
        raise err(409, "name_taken", "Bu jamoa nomi band.")

    size = team_size(t.format)
    try:
        async with session.begin_nested():
            team = Team(
                tournament_id=t.id, name=name, captain_id=user.id,
                status="full" if size == 1 else "forming",
            )
            session.add(team)
            await session.flush()
            session.add(
                TeamMember(team_id=team.id, tournament_id=t.id, user_id=user.id)
            )
            await session.flush()
    except IntegrityError:
        raise err(409, "duplicate", "Jamoa yaratib bo'lmadi: nom band yoki siz jamoadasiz.")
    await _cancel_user_invites(session, t.id, user.id)
    return team


async def send_invite(
    session: AsyncSession, captain: User, team_id: int, invitee_id: int
) -> TeamInvitation:
    team, t = await _team_and_tournament(session, team_id)
    if team.captain_id != captain.id:
        raise err(403, "not_captain", "Taklifni faqat jamoa kapitani yubora oladi.")
    if invitee_id == captain.id:
        raise err(400, "self_invite", "O'zingizni taklif qila olmaysiz.")

    size = team_size(t.format)
    n = await _member_count(session, team.id)
    if n >= size:
        raise err(409, "team_full", "Jamoa to'lgan.")

    invitee = await session.get(User, invitee_id)
    if invitee is None or not await _is_registered(session, t.id, invitee_id):
        raise err(
            404, "player_not_found",
            "O'yinchi topilmadi yoki bu turnirda ro'yxatdan o'tmagan.",
        )
    if await _membership(session, t.id, invitee_id):
        raise err(409, "player_in_team", "Bu o'yinchi allaqachon jamoada.")

    await expire_stale(session)
    dup = await session.scalar(
        select(TeamInvitation.id).where(
            TeamInvitation.team_id == team.id,
            TeamInvitation.invitee_id == invitee_id,
            TeamInvitation.status == "pending",
        )
    )
    if dup:
        raise err(409, "already_invited", "Bu o'yinchiga taklif allaqachon yuborilgan.")
    pending = await session.scalar(
        select(func.count()).select_from(TeamInvitation).where(
            TeamInvitation.team_id == team.id, TeamInvitation.status == "pending"
        )
    )
    if pending >= size - n:
        raise err(
            409, "too_many_invites",
            "Barcha bo'sh joylar uchun taklif yuborilgan. Avval birini bekor qiling.",
        )

    inv = TeamInvitation(
        team_id=team.id, invitee_id=invitee_id, status="pending",
        expires_at=_now() + INVITE_TTL,
    )
    try:
        async with session.begin_nested():
            session.add(inv)
            await session.flush()
    except IntegrityError:
        raise err(409, "already_invited", "Bu o'yinchiga taklif allaqachon yuborilgan.")
    return inv


async def respond_invite(
    session: AsyncSession, user: User, inv_id: int, accept: bool
) -> TeamInvitation:
    inv = await session.get(TeamInvitation, inv_id)
    if inv is None or inv.invitee_id != user.id:
        raise err(404, "not_found", "Taklif topilmadi.")
    team = await session.get(Team, inv.team_id)
    if team is None:
        raise err(404, "not_found", "Taklif topilmadi.")
    t = await _lock_tournament(session, team.tournament_id)
    await session.refresh(inv)

    if inv.status != "pending":
        raise err(
            409, "invite_not_pending",
            f"Taklif allaqachon {_STATUS_UZ.get(inv.status, inv.status)}.",
        )
    if _aware(inv.expires_at) < _now():
        inv.status = "expired"
        await session.commit()  # xatodan oldin holatni saqlaymiz
        raise err(409, "invite_expired", "Taklif muddati tugagan.")

    if not accept:
        inv.status = "declined"
        inv.responded_at = _now()
        await session.flush()
        return inv

    if t.status not in ROSTER_OPEN:
        raise err(409, "roster_locked", "Hozir jamoa tarkibini o'zgartirib bo'lmaydi.")
    if not await _is_registered(session, t.id, user.id):
        raise err(403, "not_registered", "Siz bu turnirda ro'yxatdan o'tmagansiz.")
    if await _membership(session, t.id, user.id):
        raise err(409, "already_in_team", "Siz allaqachon jamoadasiz.")
    await session.refresh(team)
    size = team_size(t.format)
    if await _member_count(session, team.id) >= size:
        raise err(409, "team_full", "Jamoa to'lgan.")

    try:
        async with session.begin_nested():
            session.add(
                TeamMember(team_id=team.id, tournament_id=t.id, user_id=user.id)
            )
            await session.flush()
    except IntegrityError:
        raise err(409, "already_in_team", "Siz allaqachon jamoadasiz.")

    inv.status = "accepted"
    inv.responded_at = _now()
    await session.flush()
    await _sync_status(session, team, size)
    await _cancel_user_invites(session, t.id, user.id)
    if team.status == "full":
        await _cancel_team_invites(session, team.id)
    return inv


async def cancel_invite(
    session: AsyncSession, actor: User, inv_id: int, is_admin: bool = False
) -> TeamInvitation:
    inv = await session.get(TeamInvitation, inv_id)
    if inv is None:
        raise err(404, "not_found", "Taklif topilmadi.")
    team = await _load_team(session, inv.team_id)
    await _lock_tournament(session, team.tournament_id)
    await session.refresh(inv)
    if not (is_admin or team.captain_id == actor.id):
        raise err(403, "not_captain", "Taklifni faqat kapitan bekor qila oladi.")
    if inv.status != "pending":
        raise err(
            409, "invite_not_pending",
            f"Taklif allaqachon {_STATUS_UZ.get(inv.status, inv.status)}.",
        )
    inv.status = "cancelled"
    inv.responded_at = _now()
    await session.flush()
    return inv


async def remove_member(
    session: AsyncSession, actor: User, team_id: int, user_id: int,
    is_admin: bool = False,
) -> str:
    team, t = await _team_and_tournament(session, team_id)
    if not (is_admin or team.captain_id == actor.id or user_id == actor.id):
        raise err(403, "forbidden", "Bu amal uchun ruxsat yo'q.")
    member = await session.scalar(
        select(TeamMember).where(
            TeamMember.team_id == team.id, TeamMember.user_id == user_id
        )
    )
    if member is None:
        raise err(404, "not_found", "Bu o'yinchi jamoada yo'q.")

    if user_id == team.captain_id:
        if await _member_count(session, team.id) == 1:
            await _delete_team(session, team.id)
            return "disbanded"
        raise err(
            409, "captain_cannot_leave",
            "Kapitan chiqib keta olmaydi: avval kapitanlikni boshqa a'zoga "
            "topshiring yoki jamoani tarqating.",
        )
    await session.delete(member)
    await session.flush()
    await _sync_status(session, team, team_size(t.format))
    return "removed"


async def set_captain(
    session: AsyncSession, actor: User, team_id: int, new_captain_id: int,
    is_admin: bool = False,
) -> Team:
    team, _ = await _team_and_tournament(session, team_id)
    if not (is_admin or team.captain_id == actor.id):
        raise err(403, "not_captain", "Kapitanlikni faqat kapitan topshira oladi.")
    if new_captain_id == team.captain_id:
        raise err(409, "already_captain", "Bu o'yinchi allaqachon kapitan.")
    member = await session.scalar(
        select(TeamMember).where(
            TeamMember.team_id == team.id, TeamMember.user_id == new_captain_id
        )
    )
    if member is None:
        raise err(409, "not_a_member", "Yangi kapitan jamoa a'zosi bo'lishi kerak.")
    team.captain_id = new_captain_id
    await session.flush()
    return team


async def disband_team(
    session: AsyncSession, actor: User, team_id: int, is_admin: bool = False
) -> None:
    team, _ = await _team_and_tournament(session, team_id)
    if not (is_admin or team.captain_id == actor.id):
        raise err(403, "not_captain", "Jamoani faqat kapitan tarqata oladi.")
    await _delete_team(session, team.id)


async def admin_add_member(
    session: AsyncSession, team_id: int, user_id: int
) -> Team:
    team, t = await _team_and_tournament(session, team_id)
    size = team_size(t.format)
    if await _member_count(session, team.id) >= size:
        raise err(409, "team_full", "Jamoa to'lgan.")
    if not await _is_registered(session, t.id, user_id):
        raise err(404, "player_not_found", "O'yinchi bu turnirda ro'yxatdan o'tmagan.")
    if await _membership(session, t.id, user_id):
        raise err(409, "player_in_team", "Bu o'yinchi allaqachon jamoada.")
    try:
        async with session.begin_nested():
            session.add(TeamMember(team_id=team.id, tournament_id=t.id, user_id=user_id))
            await session.flush()
    except IntegrityError:
        raise err(409, "player_in_team", "Bu o'yinchi allaqachon jamoada.")
    await _sync_status(session, team, size)
    await _cancel_user_invites(session, t.id, user_id)
    if team.status == "full":
        await _cancel_team_invites(session, team.id)
    return team


# ---------- ko'rinishlar ----------
async def team_view(
    session: AsyncSession, team: Team, size: int, detailed: bool = False
) -> dict:
    rows = (
        await session.execute(
            select(User, PlayerProfile)
            .join(TeamMember, TeamMember.user_id == User.id)
            .outerjoin(PlayerProfile, PlayerProfile.user_id == User.id)
            .where(TeamMember.team_id == team.id)
            .order_by(TeamMember.id)
        )
    ).all()
    members = []
    for u, p in rows:
        m = {
            "user_id": u.id,
            "nick": p.mlbb_nick if p else (u.first_name or "—"),
            "is_captain": u.id == team.captain_id,
        }
        if detailed:
            m.update(
                mlbb_id=p.mlbb_id if p else None,
                server_id=p.server_id if p else None,
                contact=p.contact if p else None,
            )
        members.append(m)
    return {
        "id": team.id, "tournament_id": team.tournament_id, "name": team.name,
        "status": team.status, "captain_id": team.captain_id, "size": size,
        "members_count": len(members), "open_slots": max(size - len(members), 0),
        "members": members,
    }


async def public_team_list(session: AsyncSession, t: Tournament) -> list[dict]:
    size = team_size(t.format)
    teams = list(
        await session.scalars(
            select(Team).where(Team.tournament_id == t.id).order_by(Team.id)
        )
    )
    counts = dict(
        (
            await session.execute(
                select(TeamMember.team_id, func.count())
                .where(TeamMember.tournament_id == t.id)
                .group_by(TeamMember.team_id)
            )
        ).all()
    )
    cap_ids = [x.captain_id for x in teams]
    nicks = {}
    if cap_ids:
        nicks = dict(
            (
                await session.execute(
                    select(PlayerProfile.user_id, PlayerProfile.mlbb_nick).where(
                        PlayerProfile.user_id.in_(cap_ids)
                    )
                )
            ).all()
        )
    return [
        {
            "id": x.id, "name": x.name, "status": x.status, "size": size,
            "captain_nick": nicks.get(x.captain_id),
            "members_count": counts.get(x.id, 0),
            "open_slots": max(size - counts.get(x.id, 0), 0),
        }
        for x in teams
    ]


async def free_players(session: AsyncSession, t: Tournament, me: User) -> list[dict]:
    in_team = select(TeamMember.user_id).where(TeamMember.tournament_id == t.id)
    rows = (
        await session.execute(
            select(User.id, PlayerProfile.mlbb_nick, Registration.seq_no)
            .join(Registration, Registration.user_id == User.id)
            .join(PlayerProfile, PlayerProfile.user_id == User.id)
            .where(
                Registration.tournament_id == t.id,
                Registration.status == "registered",
                User.id != me.id,
                User.id.not_in(in_team),
            )
            .order_by(Registration.seq_no)
        )
    ).all()
    return [{"user_id": uid, "nick": nick, "seq_no": seq} for uid, nick, seq in rows]


async def my_teams(session: AsyncSession, user: User) -> list[dict]:
    await expire_stale(session)
    rows = (
        await session.execute(
            select(Team, Tournament)
            .join(TeamMember, TeamMember.team_id == Team.id)
            .join(Tournament, Tournament.id == Team.tournament_id)
            .where(TeamMember.user_id == user.id)
            .order_by(Team.id.desc())
        )
    ).all()
    out = []
    for team, t in rows:
        v = await team_view(session, team, team_size(t.format), detailed=True)
        v["tournament_name"] = t.name
        v["tournament_status"] = t.status
        v["pending_invites"] = []
        if team.captain_id == user.id:
            inv_rows = await session.execute(
                select(TeamInvitation, PlayerProfile.mlbb_nick)
                .join(PlayerProfile, PlayerProfile.user_id == TeamInvitation.invitee_id)
                .where(
                    TeamInvitation.team_id == team.id,
                    TeamInvitation.status == "pending",
                )
                .order_by(TeamInvitation.id)
            )
            v["pending_invites"] = [
                {
                    "id": i.id, "invitee_id": i.invitee_id, "nick": nick,
                    "expires_at": _aware(i.expires_at).isoformat(),
                }
                for i, nick in inv_rows
            ]
        out.append(v)
    return out


async def my_invitations(session: AsyncSession, user: User) -> list[dict]:
    await expire_stale(session)
    rows = (
        await session.execute(
            select(TeamInvitation, Team, Tournament)
            .join(Team, Team.id == TeamInvitation.team_id)
            .join(Tournament, Tournament.id == Team.tournament_id)
            .where(
                TeamInvitation.invitee_id == user.id,
                TeamInvitation.status == "pending",
            )
            .order_by(TeamInvitation.id.desc())
        )
    ).all()
    out = []
    for inv, team, t in rows:
        cap_nick = await session.scalar(
            select(PlayerProfile.mlbb_nick).where(
                PlayerProfile.user_id == team.captain_id
            )
        )
        n = await _member_count(session, team.id)
        out.append(
            {
                "id": inv.id, "team_id": team.id, "team_name": team.name,
                "tournament_id": t.id, "tournament_name": t.name,
                "captain_nick": cap_nick, "members_count": n,
                "size": team_size(t.format),
                "expires_at": _aware(inv.expires_at).isoformat(),
            }
        )
    return out
