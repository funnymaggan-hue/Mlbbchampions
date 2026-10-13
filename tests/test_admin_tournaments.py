from sqlalchemy import update

from app.db import SessionLocal
from app.models import Tournament
from tests.helpers import headers

T = {"name": "Spring Cup", "format": "2v2", "max_participants": 16}
A1, A2, USER = 1001, 1002, 5555


async def _create(client, **extra):
    r = await client.post(
        "/api/admin/tournaments", json={**T, **extra}, headers=headers(A1)
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_create_hidden_until_opened(client):
    t = await _create(client)
    assert t["status"] == "draft" and t["team_size"] == 2 and t["version"] == 1
    assert (await client.get("/api/tournaments", headers=headers(USER))).json() == []
    r = await client.post(
        f"/api/admin/tournaments/{t['id']}/status",
        json={"status": "reg_open"}, headers=headers(A2),
    )
    assert r.status_code == 200 and r.json()["status"] == "reg_open"
    assert len((await client.get("/api/tournaments", headers=headers(USER))).json()) == 1


async def test_regular_user_forbidden(client):
    r = await client.post("/api/admin/tournaments", json=T, headers=headers(USER))
    assert r.status_code == 403
    r = await client.get("/api/admin/tournaments", headers=headers(USER))
    assert r.status_code == 403


async def test_validation(client):
    r = await client.post(
        "/api/admin/tournaments", json={**T, "format": "4v4"}, headers=headers(A1)
    )
    assert r.status_code == 422
    r = await client.post(
        "/api/admin/tournaments",
        json={**T, "reg_start": "2026-11-10T10:00:00Z", "reg_end": "2026-11-09T10:00:00Z"},
        headers=headers(A1),
    )
    assert r.status_code == 422


async def test_status_transitions(client):
    t = await _create(client)
    url = f"/api/admin/tournaments/{t['id']}/status"
    r = await client.post(url, json={"status": "paired"}, headers=headers(A1))
    assert r.status_code == 409
    r = await client.post(url, json={"status": "running"}, headers=headers(A1))
    assert r.status_code == 409
    assert (
        await client.post(url, json={"status": "cancelled"}, headers=headers(A1))
    ).status_code == 200
    r = await client.post(url, json={"status": "reg_open"}, headers=headers(A1))
    assert r.status_code == 409  # bekor qilingan turnir qayta ochilmaydi


async def test_two_admins_version_conflict(client):
    t = await _create(client)
    url = f"/api/admin/tournaments/{t['id']}"
    r1 = await client.patch(url, json={"name": "Cup A", "version": 1}, headers=headers(A1))
    assert r1.status_code == 200 and r1.json()["version"] == 2
    r2 = await client.patch(url, json={"name": "Cup B", "version": 1}, headers=headers(A2))
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "version_conflict"
    # ikkinchi admin yangilangan holatni ko'radi
    got = (await client.get(url, headers=headers(A2))).json()
    assert got["name"] == "Cup A"


async def test_structure_locked_after_start(client):
    t = await _create(client)
    url = f"/api/admin/tournaments/{t['id']}"
    async with SessionLocal() as s:
        await s.execute(update(Tournament).values(status="running"))
        await s.commit()
    r = await client.patch(url, json={"format": "3v3"}, headers=headers(A1))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "locked"
    r = await client.patch(url, json={"prizes": "1000$"}, headers=headers(A1))
    assert r.status_code == 200


async def test_audit_written(client):
    t = await _create(client)
    await client.patch(
        f"/api/admin/tournaments/{t['id']}", json={"name": "New Name"}, headers=headers(A2)
    )
    log = (await client.get("/api/admin/audit", headers=headers(A1))).json()
    actions = [(a["action"], a["admin_id"]) for a in log]
    assert ("tournament.update", A2) in actions
    assert ("tournament.create", A1) in actions
