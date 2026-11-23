import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, User

from backend.bot_schedule import (
    ACCESS_DENIED, due_bell, format_alert, format_schedule, read_for_account, router, send_notifications,
)
from backend.timetable import MINSK


MONDAY = date(2026, 11, 23)
SATURDAY = date(2026, 11, 28)
SUNDAY = date(2026, 11, 29)


def lesson(slot, subgroup, subject, auditorium=""):
    return {"lesson_num": slot, "subgroup": subgroup, "subject": subject, "auditorium": auditorium}


class BotScheduleTests(unittest.TestCase):
    def test_nextday_uses_sunday_when_schedule_is_published(self):
        rows = [lesson(1, 0, "Математика", "301")]
        self.assertIn("2026-11-29", format_schedule(SATURDAY, True, 0, {SUNDAY: rows}))
        self.assertIn("2026-11-30", format_schedule(SATURDAY, True, 0, {}))

    def test_schedule_filters_other_subgroup_but_keeps_shared_lesson(self):
        rows = [lesson(1, 1, "История"), lesson(1, 2, "Русский"), lesson(2, 0, "Математика", "301")]
        result = format_schedule(MONDAY, False, 2, {MONDAY: rows})
        self.assertNotIn("История", result)
        self.assertIn("Русский", result)
        self.assertIn("Математика", result)
        self.assertIn("301", result)

    def test_bell_uses_weekday_and_saturday_end_times(self):
        for day, minute, expected in ((MONDAY, 15, (6, "13:30")), (SATURDAY, 5, (6, "13:20"))):
            with self.subTest(day=day):
                self.assertEqual(due_bell(datetime(2026, 11, day.day, 13, minute, 59, tzinfo=MINSK)), expected)
        self.assertIsNone(due_bell(datetime(2026, 11, 29, 7, 45, tzinfo=MINSK)))
        self.assertIsNone(due_bell(datetime(2026, 11, 23, 7, 46, tzinfo=MINSK)))

    def test_first_break_gap_and_final_alert_respect_subgroup(self):
        rows = [lesson(1, 1, "История"), lesson(2, 2, "Русский"), lesson(4, 0, "Алгебра")]
        self.assertIsNone(format_alert(rows, 2, 0, "08:00"))
        self.assertIn("История", format_alert(rows, 1, 0, "08:00"))
        self.assertIn("Русский", format_alert(rows, 2, 1, "08:45"))
        self.assertIn("Перерыв", format_alert(rows, 2, 2, "09:40"))
        self.assertIn("Алгебра", format_alert(rows, 2, 3, "10:35"))
        self.assertIn("заканчиваются", format_alert(rows, 2, 4, "11:30"))
        self.assertIsNone(format_alert(rows, 2, 5, "12:25"))

    def test_unauthorized_account_cannot_load_schedule_rows(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        with (patch("backend.bot_schedule.connect") as connect,
              patch("backend.bot_schedule.read_schedule") as read):
            connect.return_value.__enter__.return_value = conn
            self.assertEqual(read_for_account(100, MONDAY, False), ACCESS_DENIED)
        connect.assert_called_once_with("prod")
        read.assert_not_called()


class BotDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_router_ignores_group_chats(self):
        user = User(id=100, is_bot=False, first_name="Student")
        group_message = Message(
            message_id=1, date=datetime.now(MINSK), from_user=user,
            chat=Chat(id=-100, type="group"), text="/today",
        )
        private_message = group_message.model_copy(update={"chat": Chat(id=100, type="private")})
        bot = AsyncMock(spec=Bot)

        with (patch("backend.bot_schedule.read_for_account", return_value="Расписание") as read,
              patch.object(Message, "answer", new_callable=AsyncMock) as answer):
            await router.propagate_event(update_type="message", event=group_message, bot=bot)
            read.assert_not_called()
            answer.assert_not_awaited()
            await router.propagate_event(update_type="message", event=private_message, bot=bot)
            read.assert_called_once()
            answer.assert_awaited_once_with("Расписание", parse_mode=None)

    async def test_subgroup_buttons_require_private_chat_and_known_value(self):
        user = User(id=100, is_bot=False, first_name="Student")
        private_message = Message(message_id=1, date=datetime.now(MINSK), chat=Chat(id=100, type="private"))
        query = CallbackQuery(id="query", from_user=user, chat_instance="chat", message=private_message)
        invalid = query.model_copy(update={"data": "set_sub_3"})
        group_message = private_message.model_copy(update={"chat": Chat(id=-100, type="group")})
        group = query.model_copy(update={"message": group_message, "data": "set_sub_2"})
        valid = query.model_copy(update={"data": "set_sub_2"})

        with (patch("backend.bot_schedule.save_subgroup", return_value=True) as save,
              patch.object(CallbackQuery, "answer", new_callable=AsyncMock) as answer):
            await router.propagate_event(update_type="callback_query", event=invalid)
            await router.propagate_event(update_type="callback_query", event=group)
            save.assert_not_called()
            await router.propagate_event(update_type="callback_query", event=valid)
            save.assert_called_once_with(100, 2)
            answer.assert_awaited_once_with("Сохранено: 2-я подгруппа", show_alert=True)

    async def test_notification_respects_a_preexisting_claim(self):
        now = datetime(2026, 11, 23, 7, 45, tzinfo=MINSK)
        bot = AsyncMock(spec=Bot)
        rows = [lesson(1, 0, "Алгебра", "301")]
        accounts = [{"telegram_id": 100, "subgroup": 1}, {"telegram_id": 101, "subgroup": 2}]

        with (patch("backend.bot_schedule.notification_data", return_value=(rows, accounts)),
              patch("backend.bot_schedule.datetime") as clock,
              patch("backend.bot_schedule.claim_notification", side_effect=[False, True]) as claim):
            clock.now.return_value = now
            await send_notifications(bot, now, 0, "08:00")

        self.assertEqual(claim.call_count, 2)
        bot.send_message.assert_awaited_once()
        self.assertEqual(bot.send_message.call_args.args[0], 101)
