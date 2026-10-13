from tests.flows import (
    ADMIN, admin_status, bracket, confirm, draft, find_match, set_score,
    solo_teams, start_tournament,
)
from tests.helpers import headers


async def running(client, make_tournament, n, **kw):
    tid = await make_tournament(format="1v1", **kw)
    teams = await solo_teams(client, tid, range(3001, 3001 + n))
    await start_tournament(client, tid)
    return tid, teams


def submit(client, tg, match_id, s1, s2):
    return client.post(
        f"/api/matches/{match_id}/result",
        json={"team1_score": s1, "team2_score": s2},
        headers=headers(tg),
    )


async def test_captain_submit_admin_approve_advances(client, make_tournament):
    tid, teams = await running(client, make_tournament, 4)
    tg_of = {v: k for k, v in teams.items()}
    m = (await bracket(client, tid))["rounds"][0]["matches"][0]
    t1, t2 = m["team1"]["id"], m["team2"]["id"]
    other = next(k for k, v in teams.items() if v not in (t1, t2))

    assert (await submit(client, other, m["id"], 2, 0)).status_code == 403
    r = await submit(client, tg_of[t1], m["id"], 2, 0)
    assert r.status_code == 200 and r.json()["match_status"] == "awaiting"
    r = await submit(client, tg_of[t1], m["id"], 2, 0)  # takroriy yuborish
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_submitted"

    mine = (await client.get("/api/my/matches", headers=headers(tg_of[t1]))).json()
    assert mine[0]["my_pending_result"] is True and mine[0]["can_submit"] is False

    # tasdiqlanmaguncha keyingi bosqich yangilanmaydi
    r2 = (await bracket(client, tid))["rounds"][1]["matches"][0]
    assert r2["team1"] is None

    pend = (
        await client.get(f"/api/admin/tournaments/{tid}/results/pending", headers=headers(ADMIN))
    ).json()
    assert len(pend) == 1 and pend[0]["team1_score"] == 2
    rid = pend[0]["id"]
    r = await client.post(f"/api/admin/results/{rid}/approve", headers=headers(1002))
    assert r.status_code == 200
    assert r.json()["match"]["winner_team_id"] == t1
    assert r.json()["match"]["status"] == "finished"

    r = await client.post(f"/api/admin/results/{rid}/approve", headers=headers(ADMIN))
    assert r.status_code == 409  # ikki marta qayta ishlanmaydi

    r2 = (await bracket(client, tid))["rounds"][1]["matches"][0]
    assert r2["team1"]["id"] == t1
    res = (await client.get(f"/api/tournaments/{tid}/results", headers=headers(9999))).json()
    assert len(res) == 1 and res[0]["score"] == {"team1": 2, "team2": 0}


async def test_elimination_tie_rejected(client, make_tournament):
    tid, teams = await running(client, make_tournament, 4)
    tg_of = {v: k for k, v in teams.items()}
    m = (await bracket(client, tid))["rounds"][0]["matches"][0]
    r = await submit(client, tg_of[m["team1"]["id"]], m["id"], 1, 1)
    assert r.status_code == 422


async def test_admin_only_policy(client, make_tournament):
    tid, teams = await running(client, make_tournament, 4, result_policy="admin_only")
    tg_of = {v: k for k, v in teams.items()}
    m = (await bracket(client, tid))["rounds"][0]["matches"][0]
    r = await submit(client, tg_of[m["team1"]["id"]], m["id"], 2, 0)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "admin_only"


async def test_submit_requires_running(client, make_tournament):
    tid = await make_tournament(format="1v1")
    teams = await solo_teams(client, tid, range(3001, 3005))
    await admin_status(client, tid, "reg_closed")
    await draft(client, tid)
    await confirm(client, tid)  # 'paired', hali boshlanmagan
    tg_of = {v: k for k, v in teams.items()}
    m = (await bracket(client, tid))["rounds"][0]["matches"][0]
    r = await submit(client, tg_of[m["team1"]["id"]], m["id"], 2, 0)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_running"


async def test_reject_allows_resubmit(client, make_tournament):
    tid, teams = await running(client, make_tournament, 4)
    tg_of = {v: k for k, v in teams.items()}
    m = (await bracket(client, tid))["rounds"][0]["matches"][0]
    cap = tg_of[m["team1"]["id"]]
    await submit(client, cap, m["id"], 0, 2)
    rid = (
        await client.get(f"/api/admin/tournaments/{tid}/results/pending", headers=headers(ADMIN))
    ).json()[0]["id"]
    r = await client.post(
        f"/api/admin/results/{rid}/reject", json={"reason": "dalil yo'q"}, headers=headers(ADMIN)
    )
    assert r.status_code == 200 and r.json()["match"]["status"] == "scheduled"
    assert (await submit(client, cap, m["id"], 2, 1)).status_code == 200


async def test_full_tournament_to_champion(client, make_tournament):
    tid, _ = await running(client, make_tournament, 4)
    br = await bracket(client, tid)
    m1, m2 = br["rounds"][0]["matches"]
    x1, y1 = m1["team1"]["id"], m1["team2"]["id"]
    x2, y2 = m2["team1"]["id"], m2["team2"]["id"]
    assert (await set_score(client, tid, x1, y1, 2, 0)).status_code == 200
    assert (await set_score(client, tid, x2, y2, 2, 1)).status_code == 200

    final = (await bracket(client, tid))["rounds"][1]["matches"][0]
    assert {final["team1"]["id"], final["team2"]["id"]} == {x1, x2}
    r = await set_score(client, tid, x1, x2, 0, 2)
    assert r.status_code == 200
    t = (await client.get(f"/api/admin/tournaments/{tid}", headers=headers(ADMIN))).json()
    assert t["status"] == "finished"
    final = (await bracket(client, tid))["rounds"][1]["matches"][0]
    assert final["winner_team_id"] == x2


async def test_correction_rules(client, make_tournament):
    tid, _ = await running(client, make_tournament, 4)
    m1, m2 = (await bracket(client, tid))["rounds"][0]["matches"]
    a1, a2 = m1["team1"]["id"], m1["team2"]["id"]
    b1, b2 = m2["team1"]["id"], m2["team2"]["id"]

    assert (await set_score(client, tid, a1, a2, 2, 0)).status_code == 200
    # keyingi o'yin hali boshlanmagan: g'olibni tuzatish mumkin
    assert (await set_score(client, tid, a1, a2, 0, 2)).status_code == 200
    final = (await bracket(client, tid))["rounds"][1]["matches"][0]
    assert final["team1"]["id"] == a2

    await set_score(client, tid, b1, b2, 2, 0)
    assert (await set_score(client, tid, a2, b1, 2, 0)).status_code == 200  # final

    # g'olibni o'zgartirish: keyingi o'yinga natija kiritilgan -> rad
    r = await set_score(client, tid, a1, a2, 2, 0)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "next_started"
    # g'olib o'zgarmasa, hisobni tuzatish mumkin
    assert (await set_score(client, tid, a1, a2, 1, 3)).status_code == 200

    log = (await client.get("/api/admin/audit", headers=headers(ADMIN))).json()
    fails = [a for a in log if a["action"] == "result.admin_set" and a["result"] == "fail"]
    assert len(fails) == 1


async def test_stale_version_rejected(client, make_tournament):
    tid, _ = await running(client, make_tournament, 4)
    m = (await bracket(client, tid))["rounds"][0]["matches"][0]
    r = await client.put(
        f"/api/admin/matches/{m['id']}/result",
        json={"team1_score": 2, "team2_score": 0, "version": m["version"] + 5},
        headers=headers(ADMIN),
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "version_conflict"


async def test_group_standings_and_finish(client, make_tournament):
    tid = await make_tournament(format="1v1", stage_type="group")
    teams = await solo_teams(client, tid, range(3001, 3004))
    a, b, c = teams.values()
    await start_tournament(client, tid, method="manual", group_teams=[[a, b, c]])

    assert (await set_score(client, tid, a, b, 2, 0)).status_code == 200
    assert (await set_score(client, tid, a, c, 2, 1)).status_code == 200
    t = (await client.get(f"/api/admin/tournaments/{tid}", headers=headers(ADMIN))).json()
    assert t["status"] == "running"
    assert (await set_score(client, tid, b, c, 1, 1)).status_code == 200  # durang mumkin

    st = (await client.get(f"/api/tournaments/{tid}/standings", headers=headers(9999))).json()
    rows = st["groups"][0]["rows"]
    assert [r["team_id"] for r in rows] == [a, c, b]
    assert rows[0]["points"] == 6 and rows[2]["drawn"] == 1

    t = (await client.get(f"/api/admin/tournaments/{tid}", headers=headers(ADMIN))).json()
    assert t["status"] == "finished"  # barcha o'yinlar tugadi
