import hashlib
import hmac
import json
import unittest
from urllib.parse import urlencode

from backend.telegram_auth import MAX_AGE, telegram_id_from_init_data

TOKEN = "test:bot-token"
NOW = 1795420000


def signed(user_id=105, date=NOW):
    fields = {
        "auth_date": str(date),
        "user": json.dumps({"id": user_id, "first_name": "Тест & Плюс+"}),
        "signature": "telegram-additional-signature",
    }
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    message = "\n".join(f"{name}={fields[name]}" for name in sorted(fields))
    fields["hash"] = hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class TelegramAuthTests(unittest.TestCase):
    def test_valid_signed_identity(self):
        self.assertEqual(telegram_id_from_init_data(signed(), TOKEN, NOW), 105)

    def test_reject_unsigned_tampered_and_duplicate_identity(self):
        raw = signed()
        for invalid in ("user=%7B%22id%22%3A+105%7D", raw.replace("105", "231"), raw + "&user=%7B%7D"):
            with self.subTest():
                with self.assertRaises(ValueError):
                    telegram_id_from_init_data(invalid, TOKEN, NOW)

    def test_reject_expired_future_and_non_numeric_identity(self):
        for invalid in (signed(date=NOW - MAX_AGE - 1), signed(date=NOW + 31), signed(user_id=True)):
            with self.subTest():
                with self.assertRaises(ValueError):
                    telegram_id_from_init_data(invalid, TOKEN, NOW)
