import re

from tests.helpers import headers

PROFILE = {"mlbb_nick": "Gosu", "mlbb_id": "12345678", "server_id": "2345"}
CODE_RE = re.compile(r"^MLBB-[A-Z2-9]{5}-[A-Z2-9]{5}$")


async def _profile(client, uid):
    r = await client.put("/api/profile", json=PROFILE, headers=headers(uid))
    assert r.status_code == 200, r.text


async def _register(client, tid, uid):
    return await client.post(f"/api/tournaments/{tid}/register", headers=headers(uid))


async def _add_channel(client):
    r = await client.post(
        "/api/admin/channels",
        json={
            "chat_ref": "@mlbb_news", "title": "News",
            "invite_url": "https://t.me/mlbb_news",
        },
        headers=headers(1001),
    )
    assert r.status_code == 200


async def test_requires_profile(client, make_tournament):
    tid = await make_tournament()
    r = await _register(client, tid, 2001)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "profile_required"


async def test_register_issues_ticket_and_is_idempotent(client, make_tournament):
    tid = await make_tournament()
    await _profile(client, 2001)
    r1 = await _register(client, tid, 2001)
    assert r1.status_code == 200
    reg = r1.json()["registration"]
    assert CODE_RE.match(reg["ticket_code"]) and reg["seq_no"] == 1

    r2 = await _register(client, tid, 2001)  # takroriy so'rov
    assert r2.json()["created"] is False
    assert r2.json()["registration"]["ticket_code"] == reg["ticket_code"]

    tickets = (await client.get("/api/my/tickets", headers=headers(2001))).json()
    assert len(tickets) == 1


async def test_unique_numbers_and_waitlist(client, make_tournament):
    tid = await make_tournament(max_participants=2)
    results = []
    for uid in (2001, 2002, 2003):
        await _profile(client, uid)
        results.append((await _register(client, tid, uid)).json()["registration"])
    assert [r["status"] for r in results] == ["registered", "registered", "waitlist"]
    assert [r["seq_no"] for r in results] == [1, 2, 3]
    assert len({r["ticket_code"] for r in results}) == 3


async def test_not_subscribed_blocked(client, bot, make_tournament):
    tid = await make_tournament()
    await _add_channel(client)
    await _profile(client, 2001)
    bot.status = "left"
    r = await _register(client, tid, 2001)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "not_subscribed"
    bot.status = "member"
    assert (await _register(client, tid, 2001)).status_code == 200


async def test_subscription_error_is_not_success(client, bot, make_tournament):
    tid = await make_tournament()
    await _add_channel(client)
    await _profile(client, 2001)
    bot.fail = True
    r = await _register(client, tid, 2001)
    assert r.status_code == 503
    assert (await client.get("/api/my/tickets", headers=headers(2001))).json() == []


async def test_registration_closed(client, make_tournament):
    tid = await make_tournament(status="reg_closed")
    await _profile(client, 2001)
    r = await _register(client, tid, 2001)
    assert r.status_code == 409


async def test_cancel_promotes_waitlist(client, make_tournament):
    tid = await make_tournament(max_participants=1)
    for uid in (2001, 2002):
        await _profile(client, uid)
        await _register(client, tid, uid)
    r = await client.delete(f"/api/tournaments/{tid}/register", headers=headers(2001))
    assert r.status_code == 200 and r.json()["promoted_registration_id"]
    t = (await client.get("/api/my/tickets", headers=headers(2002))).json()
    assert t[0]["status"] == "registered"
