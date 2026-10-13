from tests.flows import (
    ADMIN, admin_status, confirm, draft, full_team, mk_team, player, solo_teams,
    uid_of,
)
from tests.helpers import headers


async def test_random_pairing_8_teams(client, make_tournament):
    tid = await make_tournament(format="1v1")
    teams = await solo_teams(client, tid, range(3001, 3009))
    await admin_status(client, tid, "reg_closed")

    r = await draft(client, tid)
    assert r.status_code == 200, r.text
    ov = r.json()
    assert ov["confirmed"] is False and ov["byes"] == 0 and len(ov["rounds"]) == 3
    r1 = ov["rounds"][0]["matches"]
    assert len(r1) == 4
    used = [m[k]["id"] for m in r1 for k in ("team1", "team2")]
    assert sorted(used) == sorted(teams.values())  # har jamoa aynan bir marta

    # loyiha oddiy foydalanuvchiga ko'rinmaydi
    pub = (await client.get(f"/api/tournaments/{tid}/bracket", headers=headers(9999))).json()
    assert pub["rounds"] == []

    assert (await confirm(client, tid)).status_code == 200
    t = (await client.get(f"/api/admin/tournaments/{tid}", headers=headers(ADMIN))).json()
    assert t["status"] == "paired"
    pub = (await client.get(f"/api/tournaments/{tid}/bracket", headers=headers(9999))).json()
    assert len(pub["rounds"]) == 3


async def test_odd_teams_get_byes(client, make_tournament):
    tid = await make_tournament(format="1v1")
    await solo_teams(client, tid, range(3001, 3006))  # 5 jamoa
    await admin_status(client, tid, "reg_closed")
    ov = (await draft(client, tid)).json()
    assert ov["bracket_size"] == 8 and ov["byes"] == 3 and ov["bye_rule"]

    r1 = ov["rounds"][0]["matches"]
    assert len(r1) == 4
    byes = [m for m in r1 if m["is_bye"]]
    assert len(byes) == 3
    assert all(m["team2"] is None and m["status"] == "finished" for m in byes)

    r2 = ov["rounds"][1]["matches"]
    in_r2 = [m[k]["id"] for m in r2 for k in ("team1", "team2") if m[k]]
    assert sorted(in_r2) == sorted(m["team1"]["id"] for m in byes)


async def test_incomplete_teams_not_paired(client, make_tournament):
    tid = await make_tournament(format="2v2")
    a = await full_team(client, tid, 3001, [3002], "A")
    b = await full_team(client, tid, 3003, [3004], "B")
    await player(client, 3005, tid)
    c = (await mk_team(client, tid, 3005, "C")).json()["id"]  # faqat kapitan

    await admin_status(client, tid, "reg_closed")
    ov = (await draft(client, tid)).json()
    assert {x["id"] for x in ov["eligible"]} == {a, b}
    assert [x["id"] for x in ov["ineligible"]] == [c]

    r = await draft(
        client, tid, method="manual", pairs=[{"team1_id": a, "team2_id": c}]
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_team"


async def test_needs_two_full_teams(client, make_tournament):
    tid = await make_tournament(format="2v2")
    await full_team(client, tid, 3001, [3002], "A")
    await admin_status(client, tid, "reg_closed")
    r = await draft(client, tid)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "not_enough_teams"


async def test_manual_pairing_validation(client, make_tournament):
    tid = await make_tournament(format="1v1")
    a, b, c, d = (await solo_teams(client, tid, range(3001, 3005))).values()
    tid2 = await make_tournament(format="1v1")
    foreign = (await solo_teams(client, tid2, [3101]))[3101]
    await admin_status(client, tid, "reg_closed")

    def P(x, y):
        return {"team1_id": x, "team2_id": y}

    bad_cases = [
        [P(a, b), P(a, c)],          # takroriy jamoa
        [P(a, a), P(c, d)],          # o'ziga qarshi
        [P(a, foreign), P(c, d)],    # boshqa turnir jamoasi
        [P(a, b)],                   # joylar soni noto'g'ri
        [P(a, b), P(c, None)],       # d qolib ketgan
    ]
    for pairs in bad_cases:
        r = await draft(client, tid, method="manual", pairs=pairs)
        assert r.status_code == 422, (pairs, r.text)

    r = await draft(client, tid, method="manual", pairs=[P(c, d), P(a, b)])
    assert r.status_code == 200, r.text
    first = r.json()["rounds"][0]["matches"][0]
    assert (first["team1"]["id"], first["team2"]["id"]) == (c, d)


async def test_confirmed_pairings_protected(client, make_tournament):
    tid = await make_tournament(format="1v1")
    await solo_teams(client, tid, range(3001, 3005))
    await admin_status(client, tid, "reg_closed")
    await draft(client, tid)
    assert (await confirm(client, tid)).status_code == 200
    assert (await confirm(client, tid)).status_code == 409  # ikki marta tasdiqlanmaydi

    r = await draft(client, tid)  # o'zboshimchalik bilan qayta aralashtirib bo'lmaydi
    assert r.status_code == 409

    url = f"/api/admin/tournaments/{tid}/pairing/reset"
    r = await client.post(url, json={}, headers=headers(ADMIN))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "confirm_required"

    r = await client.post(url, json={"confirm": True}, headers=headers(1002))
    assert r.status_code == 200 and r.json()["deleted_matches"] == 3
    t = (await client.get(f"/api/admin/tournaments/{tid}", headers=headers(ADMIN))).json()
    assert t["status"] == "reg_closed"
    assert (await draft(client, tid)).status_code == 200

    log = (await client.get("/api/admin/audit", headers=headers(ADMIN))).json()
    acts = {a["action"] for a in log}
    assert {"pairing.draft", "pairing.confirm", "pairing.reset"} <= acts


async def test_confirm_rejects_team_that_lost_member(client, make_tournament):
    tid = await make_tournament(format="2v2")
    a = await full_team(client, tid, 3001, [3002], "A")
    b = await full_team(client, tid, 3003, [3004], "B")
    await admin_status(client, tid, "reg_closed")
    assert (await draft(client, tid)).status_code == 200

    mem = await uid_of(client, 3002)
    r = await client.delete(f"/api/admin/teams/{a}/members/{mem}", headers=headers(ADMIN))
    assert r.status_code == 200
    r = await confirm(client, tid)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "team_not_full"

    # loyihadagi jamoani tarqatib bo'lmaydi
    r = await client.delete(f"/api/admin/teams/{b}", headers=headers(ADMIN))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "team_in_pairing"


async def test_pairing_endpoints_admin_only(client, make_tournament):
    tid = await make_tournament(format="1v1")
    u = headers(5555)
    base = f"/api/admin/tournaments/{tid}"
    assert (await client.get(f"{base}/pairing", headers=u)).status_code == 403
    r = await client.post(f"{base}/pairing/draft", json={"method": "random"}, headers=u)
    assert r.status_code == 403
    assert (await client.post(f"{base}/pairing/confirm", headers=u)).status_code == 403
    r = await client.post(f"{base}/pairing/reset", json={"confirm": True}, headers=u)
    assert r.status_code == 403
    assert (await client.get(f"{base}/results/pending", headers=u)).status_code == 403
    assert (await client.post("/api/admin/results/1/approve", headers=u)).status_code == 403
    r = await client.put(
        "/api/admin/matches/1/result", json={"team1_score": 1, "team2_score": 0}, headers=u
    )
    assert r.status_code == 403


async def test_group_stage_round_robin(client, make_tournament):
    tid = await make_tournament(format="1v1", stage_type="group")
    await solo_teams(client, tid, range(3001, 3006))  # 5 jamoa
    await admin_status(client, tid, "reg_closed")

    r = await draft(client, tid, groups=9)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "bad_groups"

    r = await draft(client, tid, groups=1)
    assert r.status_code == 200, r.text
    ov = r.json()
    assert len(ov["rounds"]) == 5
    ms = [m for rd in ov["rounds"] for m in rd["matches"]]
    assert len([m for m in ms if not m["is_bye"]]) == 10
    assert len([m for m in ms if m["is_bye"]]) == 5  # har turda bitta jamoa dam oladi
