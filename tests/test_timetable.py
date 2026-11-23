import unittest
from datetime import datetime

from backend.timetable import MINSK, daily_schedule, schedule_state


class TimetableTests(unittest.TestCase):
    def state_at(self, day, hour, minute):
        return schedule_state(datetime(2026, 11, day, hour, minute, tzinfo=MINSK))

    def test_weekday_lunch_and_boundary(self):
        self.assertEqual(self.state_at(23, 12, 24)["current"]["kind"], "lesson")
        lunch = self.state_at(23, 12, 25)["current"]
        self.assertEqual((lunch["kind"], lunch["minutes"], lunch["end"]), ("break", 20, "12:45"))
        self.assertEqual(self.state_at(23, 12, 45)["current"]["number"], 6)

    def test_saturday(self):
        short_break = self.state_at(28, 12, 25)["current"]
        self.assertEqual((short_break["minutes"], short_break["end"]), (10, "12:35"))
        self.assertEqual(self.state_at(28, 14, 25)["current"]["number"], 8)
        self.assertIsNone(self.state_at(28, 15, 10)["current"])

    def test_sunday_and_before_class(self):
        self.assertEqual(self.state_at(29, 10, 0)["lessons"], [])
        self.assertIsNone(self.state_at(23, 7, 59)["current"])
        self.assertEqual(self.state_at(23, 7, 59)["next"]["start"], "08:00")

    def test_demo_is_stable_for_the_same_date(self):
        day = datetime(2026, 11, 23).date()
        self.assertEqual(daily_schedule(day), daily_schedule(day))
        self.assertEqual(len(daily_schedule(day)), 8)
