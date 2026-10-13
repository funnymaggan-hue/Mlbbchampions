from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.db import SessionLocal
from app.models import Tournament, TeamInvitation
from tests.helpers import headers

ADMIN = 1001


async def player(client, uid, tid):
    r = await client.put(
        "/api/profile",
        json={"mlbb_nick": f"P{uid}", "mlbb_id": "12345678", "server_id": "2345"},
        headers=headers(uid),
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/tournaments/{tid}/register", headers=headers(uid))
    assert r.status_code == 200, r.text
    return r.json()["registration"]


async def uid_of(client, tg):
    return (await client.get("/api/me", headers=headers(tg))).json()["id"]


async def mk_team(client, tid, tg, name):
    r = await client.post(
        f"/api/tournaments/{tid}/teams", json={"name": name}, headers=headers(tg)
    )
    return r


async def invite(client, team_id, cap_tg, invitee_tg):
    return await client.post(
        f"/api/teams/{team_id}/invitations",
        json={"invitee_id": await uid_of(client, invitee_tg)},
        headers=headers(cap_tg),
    )


async def full_team(client, tid, cap=2001, mem=2002, name="Alpha"):
    await player(client, cap, tid)
    await player(client, mem, tid)
    team = (await mk_team(client, tid, cap, name)).json()
    inv = (await invite(client, team["id"], cap, mem)).json()
    r = await client.post(f"/api/invitations/{inv['id']}/accept", headers=headers(mem))
    assert r.status_code == 200, r.text
    return team["id"]


async def test_only_registered_can_create(client, make_tournament):
    tid = await make_tournament(format="2v2", max_participants=1)
    await player(client, 2001, tid)  # registered
    await player(client, 2002, tid)  # waitlist
    r = await mk_team(client, tid, 2002, "Beta")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_registered"
    r = await mk_team(client, tid, 2003, "Gamma")  # umuman ro'yxatda yo'q
    assert r.status_code == 403

    r = await mk_team(client, tid, 2001, "Alpha")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "forming" and body["open_slots"] == 1
    assert body["members"][0]["is_captain"] is True
    r = await mk_team(client, tid, 2001, "Alpha2")
    assert r.status_code == 409  # bitta o'yinchi - bitta jamoa


async def test_solo_format_team_is_full_immediately(client, make_tournament):
    tid = await make_tournament(format="1v1")
    await player(client, 2001, tid)
    r = await mk_team(client, tid, 2001, "Solo")
    assert r.json()["status"] == "full" and r.json()["open_slots"] == 0


async def test_invite_accept_makes_team_full(client, make_tournament):
    tid = await make_tournament(format="2v2")
    await player(client, 2001, tid)
    await player(client, 2002, tid)
    team = (await mk_team(client, tid, 2001, "Alpha")).json()
    inv = (await invite(client, team["id"], 2001, 2002)).json()
    assert inv["status"] == "pending"

    # boshqa o'yinchi taklifni qabul qila olmaydi
    await player(client, 2003, tid)
    r = await client.post(f"/api/invitations/{inv['id']}/accept", headers=headers(2003))
    assert r.status_code == 404

    r = await client.post(f"/api/invitations/{inv['id']}/accept", headers=headers(2002))
    assert r.status_code == 200
    assert r.json()["team"]["status"] == "full"
    assert r.json()["team"]["open_slots"] == 0

    # ikki marta qabul qilish
    r = await client.post(f"/api/invitations/{inv['id']}/accept", headers=headers(2002))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "invite_not_pending"

    mine = (await client.get("/api/my/teams", headers=headers(2001))).json()
    assert len(mine[0]["members"]) == 2


async def test_only_captain_can_invite(client, make_tournament):
    tid = await make_tournament(format="3v3")
    team_id = await full_team(client, tid)  # 2 ta a'zo, 3v3 -> 1 joy bor
    await player(client, 2003, tid)
    r = await invite(client, team_id, 2002, 2003)  # oddiy a'zo
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_captain"
    r = await invite(client, team_id, 2003, 2003)  # begona
    assert r.status_code == 403


async def test_invite_rules(client, make_tournament):
    tid = await make_tournament(format="2v2")
    await player(client, 2001, tid)
    await player(client, 2002, tid)
    await player(client, 2003, tid)
    team = (await mk_team(client, tid, 2001, "Alpha")).json()
    assert (await invite(client, team["id"], 2001, 2002)).status_code == 200
    r = await invite(client, team["id"], 2001, 2002)  # takroriy
    assert r.status_code == 409
    r = await invite(client, team["id"], 2001, 2003)  # bo'sh joydan ko'p
    assert r.status_code == 409 and r.json()["detail"]["code"] == "too_many_invites"
    r = await client.post(
        f"/api/teams/{team['id']}/invitations",
        json={"invitee_id": 999999}, headers=headers(2001),
    )
    assert r.status_code == 404


async def test_decline_and_cancel(client, make_tournament):
    tid = await make_tournament(format="2v2")
    await player(client, 2001, tid)
    await player(client, 2002, tid)
    team = (await mk_team(client, tid, 2001, "Alpha")).json()
    inv = (await invite(client, team["id"], 2001, 2002)).json()
    r = await client.post(f"/api/invitations/{inv['id']}/decline", headers=headers(2002))
    assert r.json()["status"] == "declined"
    r = await client.post(f"/api/invitations/{inv['id']}/accept", headers=headers(2002))
    assert r.status_code == 409

    inv2 = (await invite(client, team["id"], 2001, 2002)).json()
    r = await client.delete(
        f"/api/teams/{team['id']}/invitations/{inv2['id']}", headers=headers(2001)
    )
    assert r.json()["status"] == "cancelled"
    r = await client.post(f"/api/invitations/{inv2['id']}/accept", headers=headers(2002))
    assert r.status_code == 409


async def test_expired_invite_rejected(client, make_tournament):
    tid = await make_tournament(format="2v2")
    await player(client, 2001, tid)
    await player(client, 2002, tid)
    team = (await mk_team(client, tid, 2001, "Alpha")).json()
    inv = (await invite(client, team["id"], 2001, 2002)).json()
    async with SessionLocal() as s:
        await s.execute(
            update(TeamInvitation).values(
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1)
            )
        )
        await s.commit()
    r = await client.post(f"/api/invitations/{inv['id']}/accept", headers=headers(2002))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "invite_expired"
    r = await client.post(f"/api/invitations/{inv['id']}/accept", headers=headers(2002))
    assert r.json()["detail"]["code"] == "invite_not_pending"  # endi 'expired' holatida


async def test_player_cannot_join_two_teams(client, make_tournament):
    tid = await make_tournament(format="2v2")
    for u in (2001, 2002, 2003):
        await player(client, u, tid)
    a = (await mk_team(client, tid, 2001, "Alpha")).json()
    b = (await mk_team(client, tid, 2003, "Beta")).json()
    inv_a = (await invite(client, a["id"], 2001, 2002)).json()
    inv_b = (await invite(client, b["id"], 2003, 2002)).json()
    assert (
        await client.post(f"/api/invitations/{inv_a['id']}/accept", headers=headers(2002))
    ).status_code == 200
    r = await client.post(f"/api/invitations/{inv_b['id']}/accept", headers=headers(2002))
    assert r.status_code == 409  # ikkinchi taklif avtomatik bekor qilingan


async def test_captain_transfer_leave_disband(client, make_tournament):
    tid = await make_tournament(format="2v2")
    team_id = await full_team(client, tid)
    cap, mem = await uid_of(client, 2001), await uid_of(client, 2002)

    r = await client.delete(f"/api/teams/{team_id}/members/{cap}", headers=headers(2001))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "captain_cannot_leave"

    r = await client.post(
        f"/api/teams/{team_id}/captain", json={"user_id": mem}, headers=headers(2002)
    )
    assert r.status_code == 403  # kapitan emas
    r = await client.post(
        f"/api/teams/{team_id}/captain", json={"user_id": mem}, headers=headers(2001)
    )
    assert r.status_code == 200 and r.json()["captain_id"] == mem

    r = await client.delete(f"/api/teams/{team_id}/members/{cap}", headers=headers(2001))
    assert r.status_code == 200 and r.json()["result"] == "removed"
    mine = (await client.get("/api/my/teams", headers=headers(2002))).json()
    assert mine[0]["status"] == "forming" and mine[0]["open_slots"] == 1

    r = await client.delete(f"/api/teams/{team_id}", headers=headers(2001))
    assert r.status_code == 403
    r = await client.delete(f"/api/teams/{team_id}", headers=headers(2002))
    assert r.status_code == 200
    # a'zolar ozod, nom qayta ishlatilishi mumkin
    assert (await mk_team(client, tid, 2002, "Alpha")).status_code == 200
    assert (await mk_team(client, tid, 2001, "Omega")).status_code == 200


async def test_roster_locked_after_pairing(client, make_tournament):
    tid = await make_tournament(format="2v2")
    await player(client, 2001, tid)
    async with SessionLocal() as s:
        await s.execute(update(Tournament).values(status="paired"))
        await s.commit()
    r = await mk_team(client, tid, 2001, "Alpha")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "roster_locked"


async def test_privacy_public_list_hides_member_details(client, make_tournament):
    tid = await make_tournament(format="2v2")
    await full_team(client, tid)
    lst = (await client.get(f"/api/tournaments/{tid}/teams", headers=headers(7777))).json()
    assert lst[0]["members_count"] == 2 and "members" not in lst[0]
    assert "12345678" not in str(lst)


async def test_admin_team_management(client, make_tournament):
    tid = await make_tournament(format="2v2")
    team_id = await full_team(client, tid)
    mem = await uid_of(client, 2002)

    # oddiy foydalanuvchi admin API'ga kira olmaydi
    r = await client.delete(f"/api/admin/teams/{team_id}", headers=headers(2001))
    assert r.status_code == 403
    r = await client.delete(
        f"/api/admin/teams/{team_id}/members/{mem}", headers=headers(2001)
    )
    assert r.status_code == 403

    r = await client.delete(
        f"/api/admin/teams/{team_id}/members/{mem}", headers=headers(ADMIN)
    )
    assert r.status_code == 200
    teams = (
        await client.get(f"/api/admin/tournaments/{tid}/teams", headers=headers(1002))
    ).json()
    assert teams[0]["members_count"] == 1 and teams[0]["status"] == "forming"

    r = await client.post(
        f"/api/admin/teams/{team_id}/members", json={"user_id": mem}, headers=headers(1002)
    )
    assert r.status_code == 200 and r.json()["status"] == "full"

    r = await client.delete(f"/api/admin/teams/{team_id}", headers=headers(ADMIN))
    assert r.status_code == 200
    assert (
        await client.get(f"/api/admin/tournaments/{tid}/teams", headers=headers(ADMIN))
    ).json() == []

    log = (await client.get("/api/admin/audit", headers=headers(ADMIN))).json()
    acts = {a["action"] for a in log}
    assert {"team.remove_member", "team.add_member", "team.disband"} <= acts
