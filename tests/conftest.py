import os

# Muhit o'zgaruvchilari app import qilinishidan OLDIN o'rnatiladi
os.environ.update(
    BOT_TOKEN="123456:TEST_TOKEN",
    ADMIN_IDS="1001,1002",
    DATABASE_URL="sqlite+aiosqlite:///./test.db",
    INITDATA_MAX_AGE="3600",
    WEBHOOK_SECRET="test-secret",
)

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.api.deps import _hits  # noqa: E402
from app.bot_instance import get_bot  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Tournament  # noqa: E402


class FakeBot:
    def __init__(self):
        self.status = "member"
        self.fail = False

    async def get_chat_member(self, chat_id, user_id):
        if self.fail:
            raise RuntimeError("telegram down")
        return SimpleNamespace(status=self.status, is_member=True)


@pytest_asyncio.fixture(autouse=True)
async def _db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    _hits.clear()
    yield
    await engine.dispose()


@pytest.fixture
def bot():
    return FakeBot()


@pytest_asyncio.fixture
async def client(bot):
    app.dependency_overrides[get_bot] = lambda: bot
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def make_tournament():
    async def _make(**kw) -> int:
        data = dict(
            name="Test Cup", format="1v1", status="reg_open", max_participants=16
        )
        data.update(kw)
        async with SessionLocal() as s:
            t = Tournament(**data)
            s.add(t)
            await s.commit()
            return t.id

    return _make
