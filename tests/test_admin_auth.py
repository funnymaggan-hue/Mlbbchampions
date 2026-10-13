from tests.helpers import headers

CH = {
    "chat_ref": "@mlbb_news",
    "title": "MLBB News",
    "invite_url": "https://t.me/mlbb_news",
}


async def test_both_admins_allowed(client):
    for uid in (1001, 1002):
        r = await client.get("/api/admin/me", headers=headers(uid))
        assert r.status_code == 200


async def test_regular_user_forbidden(client):
    r = await client.get("/api/admin/channels", headers=headers(5555))
    assert r.status_code == 403
    r = await client.post("/api/admin/channels", json=CH, headers=headers(5555))
    assert r.status_code == 403
    r = await client.get("/api/admin/audit", headers=headers(5555))
    assert r.status_code == 403


async def test_no_auth_and_forged_auth(client):
    r = await client.get("/api/admin/me")
    assert r.status_code == 401
    r = await client.get("/api/admin/me", headers=headers(1001, token="999:WRONG"))
    assert r.status_code == 401


async def test_change_visible_to_other_admin_and_audited(client):
    r = await client.post("/api/admin/channels", json=CH, headers=headers(1001))
    assert r.status_code == 200
    r = await client.get("/api/admin/channels", headers=headers(1002))
    assert [c["chat_ref"] for c in r.json()] == ["@mlbb_news"]

    r = await client.get("/api/admin/audit", headers=headers(1002))
    entry = r.json()[0]
    assert entry["admin_id"] == 1001
    assert entry["action"] == "channel.create"
    assert entry["result"] == "ok"


async def test_duplicate_channel_audited_as_fail(client):
    await client.post("/api/admin/channels", json=CH, headers=headers(1001))
    r = await client.post("/api/admin/channels", json=CH, headers=headers(1002))
    assert r.status_code == 409
    log = (await client.get("/api/admin/audit", headers=headers(1001))).json()
    assert log[0]["result"] == "fail" and log[0]["admin_id"] == 1002
