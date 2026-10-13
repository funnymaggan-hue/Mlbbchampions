import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


class InitDataError(Exception):
    pass


def validate_init_data(init_data: str, bot_token: str, max_age: int) -> dict:
    """Telegram Mini App initData imzosini tekshiradi va user dict qaytaradi."""
    if not init_data or not bot_token:
        raise InitDataError("Autentifikatsiya ma'lumoti yo'q")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataError("Imzo topilmadi")

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calculated = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise InitDataError("Imzo noto'g'ri")

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        raise InitDataError("auth_date noto'g'ri")
    now = time.time()
    if auth_date <= 0 or now - auth_date > max_age or auth_date > now + 60:
        raise InitDataError("Autentifikatsiya muddati tugagan")

    try:
        user = json.loads(pairs["user"])
        int(user["id"])
    except (KeyError, ValueError, TypeError):
        raise InitDataError("Foydalanuvchi ma'lumoti noto'g'ri")
    return user
