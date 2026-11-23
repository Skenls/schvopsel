import io
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw
from google.genai.errors import ClientError

from backend.ocr import (
    GeminiReader, Header, Location, RoomGrid, RoomRow, Row, Transcription,
    lessons_from_rows, level_group_crop, parse_image, printed_day, straight_group_crops, verify_room_grid,
)


def row(number, subject=None, room=None, kind="lesson"):
    return Row(number=number, kind=kind, subject=subject, room=room, teacher=None)


class OCRTests(unittest.TestCase):
    def setUp(self):
        image = io.BytesIO()
        Image.new("RGB", (960, 1280), "white").save(image, "PNG")
        self.image = image.getvalue()
        self.location = Location(printed_date="23.11.26г.", weekday="ПОНЕДЕЛЬНИК",
                                 group_label="11П", box_2d=[680, 490, 760, 910])

    def test_header_uses_printed_day_and_checks_weekday(self):
        self.assertEqual(printed_day("23.11.26г.", "ПОНЕДЕЛЬНИК"), date(2026, 11, 23))
        self.assertIsNone(printed_day("23.11.26г.", "ВТОРНИК"))
        self.assertIsNone(printed_day("31.11.26г."))

    def test_crops_exact_group_and_rejects_wrong_group(self):
        wrong = Transcription(group_label="13П", complete=True, rows=[row(1, "Математика")])
        actual = Transcription(group_label="11п", complete=True, rows=[row(2, "Физика", "502")])
        audit = RoomGrid(group_label="11П", rooms=[RoomRow(number=2, subgroup=0, room="502")])
        mock = Mock(side_effect=[self.location, wrong, actual, audit])
        result = parse_image(self.image, "11П", mock)
        self.assertEqual(result["date"], "2026-11-23")
        self.assertEqual(result["lessons"][0]["lesson_num"], 2)
        self.assertEqual([len(call.args) for call in mock.call_args_list], [4, 3, 3, 4])

    def test_uncertain_date_never_calls_lesson_model(self):
        mock = Mock(return_value=self.location.model_copy(update={"printed_date": None}))
        self.assertFalse(parse_image(self.image, "11П", mock)["group_found"])
        self.assertEqual([call.args[0] for call in mock.call_args_list], [Location, Header])

    def test_numberless_rows_ticks_same_subject_and_room_codes(self):
        result = lessons_from_rows([
            row(2, "Астрономия", "502"), row(4, "Алгоритмизации", "9/3"),
            row(6, "ЗащитаКомпИнфм", "408"), row(None, "ЗащитаКомпИнфм", "410"),
            row(7, "ФизичКультура"), row(None, kind="tick"),
            row(8, kind="tick"), row(None, "ИнженерГрафика", "313"),
        ])
        self.assertEqual([(r["lesson_num"], r["subgroup"], r["auditorium"]) for r in result],
                         [(2, 0, "502"), (4, 0, "ч/з"), (6, 1, "408"), (6, 2, "410"),
                          (7, 1, ""), (8, 2, "313")])

    def test_ambiguous_rows_are_rejected(self):
        for rows in ([row(None, "Физика")], [row(3, "Физика"), row(3, "Физика")],
                     [row(1, "Физика", "неразборчиво")], [row(1, kind="unclear")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                lessons_from_rows(rows)

    def test_subgroup_rooms_get_independent_check(self):
        lessons = lessons_from_rows([row(2, "Алгоритмизации", "305"), row(None, "Алгоритмизации", "305")])
        audit = RoomGrid(group_label="11П", rooms=[
            RoomRow(number=2, subgroup=1, room="410"), RoomRow(number=2, subgroup=2, room="305"),
        ])
        self.assertEqual([x["auditorium"] for x in verify_room_grid(lessons, "11П", self.image, Mock(return_value=audit))],
                         ["410", "305"])

    def test_unreadable_room_in_first_pass_uses_the_independent_grid(self):
        subject = Transcription(group_label="11П", complete=True, rows=[row(2, "Физика", "3/3")])
        grid = RoomGrid(group_label="11П", rooms=[RoomRow(number=2, subgroup=0, room="ч/з")])
        result = parse_image(self.image, "11П", Mock(side_effect=[self.location, subject, grid]))
        self.assertEqual(result["lessons"][0]["auditorium"], "ч/з")

    def test_tight_crop_uses_horizontal_borders_despite_offset_hint(self):
        image = Image.new("L", (340, 170), "white")
        draw = ImageDraw.Draw(image)
        draw.line((9, 60, 315, 60), fill=0)
        draw.line((9, 135, 315, 135), fill=0)
        crop, = straight_group_crops(image, 36, 139)
        self.assertEqual(crop.size, (340, 82))
        self.assertEqual(crop.getpixel((200, 3)), 0)

    def test_slanted_table_rules_are_leveled_before_cropping(self):
        image = Image.new("L", (320, 160), "white")
        draw = ImageDraw.Draw(image)
        for y in (38, 78, 117):
            draw.line((0, y, 319, y + 24), fill=0, width=3)
        leveled = level_group_crop(image)
        for y in (50, 90, 129):
            with self.subTest(y=y):
                self.assertLess(sum(leveled.getpixel((x, y)) for x in range(20, 300)) / 280, 45)

    def test_fallback_on_quota_and_model_unavailability(self):
        client = Mock()
        client.models.generate_content.side_effect = [
            ClientError(429, {"error": {"code": 429}}),
            ClientError(404, {"error": {"code": 404}}),
            SimpleNamespace(text=self.location.model_dump_json()),
            SimpleNamespace(text=self.location.model_dump_json()),
        ]
        reader = GeminiReader(client, ("quota", "missing", "working"))
        self.assertEqual(reader(Location, "date", b"jpeg"), self.location)
        self.assertEqual(reader(Location, "date", b"jpeg"), self.location)
        self.assertEqual([call.kwargs["model"] for call in client.models.generate_content.call_args_list],
                         ["quota", "missing", "working", "working"])

    def test_retry_transient_model_after_fallback_without_retrying_quota(self):
        client = Mock()
        client.models.generate_content.side_effect = [
            ClientError(503, {"error": {"code": 503}}),
            ClientError(429, {"error": {"code": 429}}),
            SimpleNamespace(text=self.location.model_dump_json()),
        ]
        with patch("backend.ocr.sleep") as wait:
            self.assertEqual(GeminiReader(client, ("busy", "quota"))(Location, "date", b"jpeg"), self.location)
        wait.assert_called_once_with(2)
        self.assertEqual([call.kwargs["model"] for call in client.models.generate_content.call_args_list],
                         ["busy", "quota", "busy"])
