import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.schedule_sync import PhotoReader, select_schedules
from backend.timetable import MINSK, schedule_days, schedule_state

TODAY = date(2026, 11, 23)
TOMORROW = TODAY + timedelta(days=1)
BEFORE = TODAY - timedelta(days=1)
LESSON = {"lesson_num": 2, "subgroup": 0, "subject": "Физика", "auditorium": "502", "teacher": ""}


async def posts(*albums):
    for album in albums:
        yield [SimpleNamespace(id=ident, caption="31.12.99 — wrong date") for ident in album]


class ScannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_next_day_skips_today_and_reads_both_pages(self):
        inspect = AsyncMock(side_effect=[(TODAY, True), (TOMORROW, False), (TOMORROW, True)])
        transcribe = AsyncMock(return_value=[LESSON])
        actual = [
            item async for item in select_schedules(
                posts((44,), (43, 42), (41,)), TODAY, inspect, transcribe, targets=(TOMORROW,),
            )
        ]
        self.assertEqual(actual, [(TOMORROW, 42, [LESSON])])
        self.assertEqual([call.args[0].id for call in transcribe.call_args_list], [42])

    async def test_saved_transcription_needs_no_image_download(self):
        photo = SimpleNamespace(id=42, download_media=AsyncMock())
        reader = PhotoReader("11П", None)
        reader.lessons[42] = [LESSON]
        self.assertEqual(await reader.transcribe(photo), [LESSON])
        photo.download_media.assert_not_called()

    async def test_reads_second_sheet_and_stops_on_stale_printed_date(self):
        inspect = AsyncMock(side_effect=[(TODAY, False), (TODAY, True), (BEFORE, True)])
        transcribe = AsyncMock(return_value=[LESSON])
        actual = [
            item async for item in select_schedules(posts((42, 41), (40,), (39,)), TODAY, inspect, transcribe)
        ]
        self.assertEqual(actual, [(TODAY, 41, [LESSON])])
        self.assertEqual([call.args[0].id for call in inspect.call_args_list], [42, 41, 40])
        self.assertEqual([call.args[0].id for call in transcribe.call_args_list], [41])

    async def test_unreadable_sheet_does_not_stop_and_next_is_saved(self):
        inspect = AsyncMock(side_effect=[(None, False), (TOMORROW, True), (TODAY, True)])
        transcribe = AsyncMock(return_value=[LESSON])
        actual = [
            item async for item in select_schedules(posts((5,), (4,), (3,), (2,)), TODAY, inspect, transcribe)
        ]
        self.assertEqual([item[0] for item in actual], [TOMORROW, TODAY])
        self.assertEqual(inspect.call_count, 3)


class ChannelScheduleTests(unittest.TestCase):
    def test_saturday_uses_sunday_only_if_a_sheet_was_published(self):
        saturday = date(2026, 11, 21)
        sunday = saturday + timedelta(days=1)
        self.assertEqual(schedule_days(saturday), (saturday, sunday, TODAY))
        now = datetime(2026, 11, 21, 9, tzinfo=MINSK)
        self.assertEqual(schedule_state(now, {TODAY: [LESSON]})["nextDay"]["date"], TODAY.isoformat())
        self.assertEqual(schedule_state(now, {sunday: [LESSON]})["nextDay"]["date"], sunday.isoformat())

    def test_split_variants_share_the_bell_time_and_missing_slot_is_not_a_break(self):
        rows = [
            LESSON | {"subgroup": 1, "auditorium": "410"},
            LESSON | {"subgroup": 2, "auditorium": "ч/з"},
            LESSON | {"lesson_num": 4},
        ]
        state = schedule_state(datetime(2026, 11, 23, 9, tzinfo=MINSK), {TODAY: rows})
        self.assertEqual(state["current"]["start"], "08:55")
        self.assertEqual([v["auditorium"] for v in state["current"]["variants"]], ["410", "ч/з"])
        gap = schedule_state(datetime(2026, 11, 23, 10, tzinfo=MINSK), {TODAY: rows})
        self.assertIsNone(gap["current"])
