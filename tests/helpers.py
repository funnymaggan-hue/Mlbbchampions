import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

TEST_TOKEN = "123456:TEST_TOKEN"


def make_init_data(
    user_id: int,
    token: str = TEST_TOKEN,
    auth_date: int | None = None,
    first_name: str = "Test",
    username: str | None = None,
) -> str:
    user = {"id": user_id, "first_name": first_name}
    if username:
        user["username"] = username
    fields = {
        "auth_date": str(int(time.time()) if auth_date is None else auth_date),
        "query_id": "AAHtest",
        "user": json.dumps(user, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def headers(user_id: int, **kw) -> dict:
    return {"X-Telegram-Init-Data": make_init_data(user_id, **kw)}
