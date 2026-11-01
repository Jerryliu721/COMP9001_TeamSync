# -*- coding: utf-8 -*-
"""End-to-end tests for the command-line front end.

The unit tests elsewhere call the planner and the importers directly.  These
tests instead drive ``main.main`` the way a marker would: with ``--cli`` and a
scripted sequence of answers (and, for the batch flags, with no answers at all).

Every test keeps its files in a temporary folder.  The CLI normally writes
``output/latest.json`` and ``output/meeting_<week>.<ext>``; patching
``storage.ensure_output_dir`` / ``storage.export`` redirects those writes so the
shipped demo output is never touched.
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import main
import storage

DEMO_TEAM = os.path.join(ROOT, "data", "demo_team.json")
DEMO_ICS = os.path.join(ROOT, "data", "example_calendar.ics")
DEMO_CSV = os.path.join(ROOT, "data", "example_roster.csv")


class TestCliMenu(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="teamsync_cli_")
        self.real_export = storage.export

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def run_cli(self, answers):
        """Run the menu with scripted answers; return (exit code, output)."""
        buffer = io.StringIO()
        with mock.patch("builtins.input", side_effect=answers), \
                mock.patch.object(storage, "ensure_output_dir",
                                  lambda *args, **kwargs: self.folder), \
                mock.patch.object(
                    storage, "export",
                    lambda plan, name, kind, folder=None:
                        self.real_export(plan, name, kind, folder=self.folder)):
            with contextlib.redirect_stdout(buffer):
                code = main.main(["--cli", "--file", DEMO_TEAM])
        return code, buffer.getvalue()

    def test_show_members_then_quit(self):
        code, out = self.run_cli(["1", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Alice", out)
        self.assertIn("Australia/Sydney", out)
        self.assertIn("Bye.", out)

    def test_empty_answer_quits(self):
        code, out = self.run_cli([""])
        self.assertEqual(code, 0)
        self.assertIn("Bye.", out)

    def test_bad_choice_is_reported(self):
        code, out = self.run_cli(["99", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Please pick a number between 0 and 12", out)

    def test_add_member_with_role_and_away_date(self):
        answers = ["2", "Zoe", "Mumbai", "", "10:00-12:00", "n",
                   "2026-03-04..2026-03-05", "1", "0"]
        code, out = self.run_cli(answers)
        self.assertEqual(code, 0)
        self.assertIn("Added Zoe (Mumbai, Asia/Kolkata, optional)", out)
        self.assertIn("Zoe", out)
        self.assertIn("2026-03-04, 2026-03-05", out)

    def test_plan_meeting_prints_recommendations(self):
        code, out = self.run_cli(["3", "", "", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Recommendation 1", out)
        self.assertIn("Meet at 2026-03-02 (Mon) 11:00 UTC", out)
        self.assertTrue(os.path.isfile(os.path.join(self.folder, "latest.json")))

    def test_compare_weeks(self):
        code, out = self.run_cli(["4", "2", "", "", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Week of", out)
        self.assertIn("Best week of the range", out)

    def test_check_possible_lengths(self):
        code, out = self.run_cli(["5", "", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Length   Best time", out)
        self.assertIn("60 min", out)

    def test_explain_a_time(self):
        code, out = self.run_cli(["6", "2026-03-02 11:00", "0"])
        self.assertEqual(code, 0)
        self.assertIn("4 of 4 members are free", out)
        self.assertIn("Sydney", out)

    def test_import_a_calendar_for_a_member(self):
        code, out = self.run_cli(["7", "Bo", DEMO_ICS, "1", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Imported 10 busy event(s) for Bo", out)
        self.assertIn("COMP9001 lecture", out)
        self.assertIn("10 events", out)

    def test_import_a_roster(self):
        code, out = self.run_cli(["8", DEMO_CSV, "y", "1", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Loaded 4 member(s)", out)
        self.assertIn("Eve", out)

    def test_save_then_load_a_team(self):
        path = os.path.join(self.folder, "team.json")
        code, out = self.run_cli(["10", "s", path, "10", "l", path, "0"])
        self.assertEqual(code, 0)
        self.assertIn("Saved", out)
        self.assertIn("Loaded 4 member(s)", out)
        self.assertTrue(os.path.isfile(path))

    def test_change_reference_timezone(self):
        code, out = self.run_cli(["9", "london", "3", "", "", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Reference timezone: London", out)
        self.assertIn("The same times seen from London", out)

    def test_export_the_last_result(self):
        code, out = self.run_cli(["3", "", "", "12", "csv", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Wrote", out)
        self.assertTrue(os.path.isfile(
            os.path.join(self.folder, "meeting_2026-03-02.csv")))

    def test_unknown_export_format_is_refused(self):
        code, out = self.run_cli(["3", "", "", "12", "pdf", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Pick one of:", out)

    def test_export_before_planning_is_refused(self):
        code, out = self.run_cli(["12", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Plan a meeting first", out)


class TestBatchMode(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="teamsync_batch_")
        self.real_export = storage.export

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def run_batch(self, argv):
        buffer = io.StringIO()
        with mock.patch.object(storage, "ensure_output_dir",
                               lambda *args, **kwargs: self.folder), \
                mock.patch.object(
                    storage, "export",
                    lambda plan, name, kind, folder=None:
                        self.real_export(plan, name, kind, folder=self.folder)):
            with contextlib.redirect_stdout(buffer):
                code = main.main(argv)
        return code, buffer.getvalue()

    def test_report(self):
        code, out = self.run_batch(["--report", "--file", DEMO_TEAM])
        self.assertEqual(code, 0)
        self.assertIn("TeamSync - Best meeting times", out)
        self.assertIn("Recommendation 1", out)

    def test_weeks_prints_the_comparison(self):
        code, out = self.run_batch(["--weeks", "4", "--file", DEMO_TEAM])
        self.assertEqual(code, 0)
        self.assertIn("Daylight saving changes", out)
        self.assertIn("2026-03-08", out)

    def test_export_only(self):
        code, out = self.run_batch(["--export", "csv", "--file", DEMO_TEAM])
        self.assertEqual(code, 0)
        self.assertIn("Wrote", out)
        self.assertTrue(os.path.isfile(
            os.path.join(self.folder, "meeting_2026-03-02.csv")))

    def test_week_and_duration_flags(self):
        code, out = self.run_batch(["--week", "2026-04-06", "--duration", "90",
                                    "--report", "--file", DEMO_TEAM])
        self.assertEqual(code, 0)
        self.assertIn("Week of: 2026-04-06", out)
        self.assertIn("Meeting length: 90 min", out)

    def test_missing_team_file_exits_with_one(self):
        code, out = self.run_batch(["--file", "nope.json", "--report"])
        self.assertEqual(code, 1)
        self.assertIn("!", out)
        self.assertNotIn("Traceback", out)

    def test_unknown_export_format_exits_with_one(self):
        code, out = self.run_batch(["--export", "pdf"])
        self.assertEqual(code, 1)
        self.assertIn("unknown export format", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
