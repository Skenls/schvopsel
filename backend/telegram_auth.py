import hmac
import json
import re
from time import time
from urllib.parse import parse_qsl

MAX_AGE = 3600
MAX_TELEGRAM_ID = (1 << 52) - 1


def telegram_id_from_init_data(raw: str, token: str, now: float | None = None) -> int:
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, max_num_fields=30)
    fields = dict(pairs)
    received = fields.pop("hash", "")
    if len(pairs) != len(fields) + 1 or not re.fullmatch("[0-9a-f]{64}", received):
        raise ValueError("Invalid init data")

    data = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.digest(b"WebAppData", token.encode(), "sha256")
    expected = hmac.digest(secret, data.encode(), "sha256").hex()
    if not hmac.compare_digest(received, expected):
        raise ValueError("Invalid signature")

    age = (time() if now is None else now) - int(fields.get("auth_date", "0"))
    if not -30 <= age <= MAX_AGE:
        raise ValueError("Expired init data")

    user = json.loads(fields.get("user", "null"))
    user_id = user.get("id") if isinstance(user, dict) else None
    if type(user_id) is not int or not 1 <= user_id <= MAX_TELEGRAM_ID:
        raise ValueError("Invalid Telegram ID")
    return user_id
