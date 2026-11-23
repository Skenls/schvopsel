import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.bot_admin import View, admin_command, identifier
from backend.crew_store import CrewError
from backend.telegram_auth import MAX_TELEGRAM_ID


class AdminTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_is_silent_without_authority(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=123), answer=AsyncMock())
        with patch("backend.bot_admin.execute", AsyncMock(return_value=None)):
            await admin_command(message, 456)
        message.answer.assert_not_called()

    async def test_command_links_the_inline_admin_menu(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=456), answer=AsyncMock())
        view = View("Управление", [[("Команда", "menu")]])
        with patch("backend.bot_admin.execute", AsyncMock(return_value=view)):
            await admin_command(message, 456)
        self.assertEqual(message.answer.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data,
                         "admin:menu")

    def test_telegram_id_range_and_syntax(self):
        self.assertEqual(identifier(str(MAX_TELEGRAM_ID)), MAX_TELEGRAM_ID)
        for value in ("0", "-1", "12:3", "1e2", str(MAX_TELEGRAM_ID + 1)):
            with self.subTest(value=value), self.assertRaises(CrewError):
                identifier(value)
