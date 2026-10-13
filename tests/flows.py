from tests.helpers import headers

ADMIN = 1001


async def player(client, tg, tid):
    r = await client.put(
        "/api/profile",
        json={"mlbb_nick": f"P{tg}", "mlbb_id": "12345678", "server_id": "2345"},
        headers=headers(tg),
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/tournaments/{tid}/register", headers=headers(tg))
    assert r.status_code == 200, r.text


async def uid_of(client, tg):
    return (await client.get("/api/me", headers=headers(tg))).json()["id"]


async def mk_team(client, tid, tg, name):
    return await client.post(
        f"/api/tournaments/{tid}/teams", json={"name": name}, headers=headers(tg)
    )


async def full_team(client, tid, cap, members, name):
    await player(client, cap, tid)
    team = (await mk_team(client, tid, cap, name)).json()
    for m in members:
        await player(client, m, tid)
        inv = await client.post(
            f"/api/teams/{team['id']}/invitations",
            json={"invitee_id": await uid_of(client, m)},
            headers=headers(cap),
        )
        assert inv.status_code == 200, inv.text
        r = await client.post(
            f"/api/invitations/{inv.json()['id']}/accept", headers=headers(m)
        )
        assert r.status_code == 200, r.text
    return team["id"]


async def solo_teams(client, tid, tgs) -> dict[int, int]:
    """1v1 turnirda har bir o'yinchi - alohida jamoa. {telegram_id: team_id}."""
    out = {}
    for tg in tgs:
        await player(client, tg, tid)
        r = await mk_team(client, tid, tg, f"T{tg}")
        assert r.status_code == 200, r.text
        out[tg] = r.json()["id"]
    return out


async def admin_status(client, tid, status, tg=ADMIN):
    return await client.post(
        f"/api/admin/tournaments/{tid}/status", json={"status": status},
        headers=headers(tg),
    )


async def draft(client, tid, tg=ADMIN, **body):
    payload = {"method": "random", **body}
    return await client.post(
        f"/api/admin/tournaments/{tid}/pairing/draft", json=payload, headers=headers(tg)
    )


async def confirm(client, tid, tg=ADMIN):
    return await client.post(
        f"/api/admin/tournaments/{tid}/pairing/confirm", headers=headers(tg)
    )


async def start_tournament(client, tid, **draft_kw):
    assert (await admin_status(client, tid, "reg_closed")).status_code == 200
    r = await draft(client, tid, **draft_kw)
    assert r.status_code == 200, r.text
    r = await confirm(client, tid)
    assert r.status_code == 200, r.text
    assert (await admin_status(client, tid, "running")).status_code == 200


async def bracket(client, tid):
    r = await client.get(f"/api/tournaments/{tid}/bracket", headers=headers(9999))
    assert r.status_code == 200, r.text
    return r.json()


def find_match(br, a, b):
    for rd in br["rounds"]:
        for m in rd["matches"]:
            ids = {x["id"] for x in (m["team1"], m["team2"]) if x}
            if ids == {a, b}:
                return m
    return None


async def set_score(client, tid, x, y, sx, sy, tg=ADMIN):
    """x jamoa sx, y jamoa sy hisob bilan (o'yindagi tartibdan qat'i nazar)."""
    m = find_match(await bracket(client, tid), x, y)
    assert m is not None
    s1, s2 = (sx, sy) if m["team1"]["id"] == x else (sy, sx)
    return await client.put(
        f"/api/admin/matches/{m['id']}/result",
        json={"team1_score": s1, "team2_score": s2},
        headers=headers(tg),
    )
