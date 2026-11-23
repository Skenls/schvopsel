import unittest

from backend.crew import CREW, MAIN_ADMIN_MEMBER, initial_vessel, rooms_for


class CrewTests(unittest.TestCase):
    def test_main_admin_is_an_existing_crew_member(self):
        self.assertEqual(CREW[MAIN_ADMIN_MEMBER], "Мышь")

    def test_initial_vessels(self):
        self.assertEqual(
            {name for name in CREW if initial_vessel(name) == "dinghy"},
            {"Шлюп", "Чун"},
        )

    def test_dinghy_has_only_its_deck(self):
        self.assertEqual(rooms_for("dinghy"), ("deck",))
        self.assertIn("hammam", rooms_for("ship"))
