# -*- coding: utf-8 -*-
"""Tests for the TeamSync core.

Run from the project folder with either of::

    python -m unittest discover tests
    python tests/test_planner.py

The GUI smoke test skips itself when tkinter (or a display) is missing, so the
suite stays green on the machines our markers happen to use.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import cities
import models
import planner
import storage
from models import Availability

try:
    import tkinter

    HAS_TK = True
except ImportError:                      # pragma: no cover - machine dependent
    HAS_TK = False

DEMO_TEAM = os.path.join(ROOT, "data", "demo_team.json")
WEEK_A = date(2026, 3, 2)                # the week used by every example
WEEK_B = date(2026, 4, 6)                # the contrasting week in section 4.4


def demo_roster():
    """The four-person team shipped in data/demo_team.json."""
    _, _, _, roster = storage.load_team(DEMO_TEAM)
    return roster


def member(name, start, end, city="Beijing"):
    """A member whose only window is ``start``-``end`` local time, Mon-Fri.

    Beijing is UTC+8 all year, which keeps the arithmetic in these tests boring
    on purpose -- the daylight saving cases get their own tests below.
    """
    return cities.make_member(name, city,
                              [Availability((0, 1, 2, 3, 4),
                                            models.parse_hhmm(start),
                                            models.parse_hhmm(end))])


class TestTimezones(unittest.TestCase):

    def test_load_tz_without_system_db(self):
        """The TZif files travel with the project, so no system database is needed."""
        path = os.path.join(cities.TZFILES_DIR, "Australia", "Sydney")
        self.assertTrue(os.path.isfile(path),
                        "missing data/tzfiles -- run tools/build_tzfiles.py")
        self.assertEqual(cities.load_tz("Australia/Sydney").key, "Australia/Sydney")

    def test_offset_january_vs_july(self):
        sydney = cities.load_tz("Australia/Sydney")
        january = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc).astimezone(sydney)
        july = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc).astimezone(sydney)
        self.assertEqual(january.utcoffset(), timedelta(hours=11))
        self.assertEqual(july.utcoffset(), timedelta(hours=10))

    def test_dst_transition_week(self):
        """New York jumps from UTC-5 to UTC-4 on 2026-03-08."""
        zone = cities.load_tz("America/New_York")
        before = datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc).astimezone(zone)
        after = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc).astimezone(zone)
        self.assertEqual((before.hour, before.utcoffset()), (7, timedelta(hours=-5)))
        self.assertEqual((after.hour, after.utcoffset()), (8, timedelta(hours=-4)))


class TestWindows(unittest.TestCase):

    def test_window_wraps_midnight(self):
        window = Availability((0,), 22 * 60, 2 * 60)   # Monday 22:00 -> Tuesday 02:00
        self.assertTrue(window.wraps_midnight)
        self.assertTrue(window.covers(0, 22 * 60))
        self.assertTrue(window.covers(0, 23 * 60 + 59))
        self.assertTrue(window.covers(1, 0))
        self.assertTrue(window.covers(1, 60))
        self.assertFalse(window.covers(0, 21 * 60 + 59))
        self.assertFalse(window.covers(1, 2 * 60))
        self.assertFalse(window.covers(2, 60))         # only Monday starts it

    def test_windows_text_round_trip(self):
        """The edit box in the GUI writes and reads the very same string."""
        text = "09:00-12:00 + 18:00-22:00"
        bo = models.Member("Bo", "Beijing", "Asia/Shanghai", models.parse_windows(text))
        self.assertEqual(bo.windows_text(), text)
        self.assertEqual(bo.describe_windows(),
                         "Mon-Fri 09:00-12:00 + Mon-Fri 18:00-22:00")

    def test_invalid_time_format(self):
        for bad in ("25:99", "9", "", "12:60", "aa:bb", "-1:00"):
            with self.assertRaises(models.InvalidTimeFormat):
                models.parse_hhmm(bad)

    def test_unknown_city(self):
        with self.assertRaises(cities.UnknownCity) as caught:
            cities.lookup("Sidney")
        self.assertIn("Sidney", str(caught.exception))
        self.assertIn("--cities", str(caught.exception))
        self.assertEqual(cities.lookup("sydney")[1], "Australia/Sydney")
        self.assertEqual(cities.lookup(" New-York ")[1], "America/New_York")


class TestPlanner(unittest.TestCase):

    def test_overlap_basic(self):
        roster = [member("Alice", "09:00", "12:00"),
                  member("Bo", "10:00", "13:00")]
        best = planner.plan(roster, WEEK_A, 60).best
        self.assertEqual(best.count, 2)
        self.assertEqual(best.continuous_min, 120)     # 10:00-12:00 Beijing time
        self.assertEqual(best.start.strftime("%H:%M"), "02:00")

    def test_duration_filter(self):
        roster = [member("Alice", "10:00", "10:30"),
                  member("Bo", "10:00", "12:00")]
        self.assertEqual(planner.plan(roster, WEEK_A, 60).best.count, 1)
        self.assertEqual(planner.plan(roster, WEEK_A, 30).best.count, 2)

    def test_best_start_within_segment(self):
        """The start slides inside the segment instead of hugging its first cell."""
        roster = [member("Alice", "08:00", "11:00")]   # three hours in Beijing
        result = planner.plan(roster, WEEK_A, 60)
        self.assertEqual(result.best.continuous_min, 180)
        self.assertEqual(result.best.start.strftime("%H:%M"), "01:00")   # 09:00 local
        self.assertEqual(result.best.score,
                         planner.COVERAGE_BONUS + planner.COMFORT_WORK)

    def test_score_uses_start_only(self):
        """A long meeting is judged by its start cell, not by the whole stretch."""
        roster = [member("Alice", "08:00", "11:00")]
        result = planner.plan(roster, WEEK_A, 60)
        segment = max(result.segments, key=lambda item: item.duration_min)
        self.assertEqual(segment.duration_min, 180)
        self.assertEqual(result.best.score,
                         planner.COVERAGE_BONUS + planner.COMFORT_WORK)
        self.assertGreater(sum(slot.score for slot in segment.slots),
                           result.best.score)

    def test_no_overlap_returns_empty(self):
        roster = [member("Alice", "09:00", "10:00"),
                  member("Bo", "21:00", "22:00")]
        result = planner.plan(roster, WEEK_A, 120)     # nobody stays free that long
        self.assertEqual(result.recommendations, [])
        self.assertIsNone(result.best)
        self.assertEqual(result.best_continuous_min, 60)

    def test_grid_shape(self):
        result = planner.plan(demo_roster(), WEEK_A, 60)
        rows = result.hourly_rows()
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(len(counts) == 24 for _, counts in rows))
        self.assertEqual(rows[0][0], WEEK_A)
        # an hour reports the better of its two half-hour cells
        self.assertEqual(rows[0][1][0],
                         max(result.slots[0].count, result.slots[1].count))

    def test_week_a_matches_document(self):
        """The exact numbers quoted in README.md (demo team table)."""
        result = planner.plan(demo_roster(), WEEK_A, 60)
        self.assertEqual([item.score for item in result.recommendations],
                         [205, 180, 180])
        best = result.best
        self.assertEqual(best.count, 4)
        self.assertEqual(best.start.strftime("%Y-%m-%d %H:%M"), "2026-03-02 11:00")
        self.assertEqual(best.continuous_min, 60)
        self.assertEqual([item.name for item in best.members],
                         ["Alice", "Bo", "Chen", "Dan"])

    def test_week_b_expands_window(self):
        """A month later Australia has left DST, so the shared window triples.

        Sydney (AEST +10), London (BST +1) and New York (EDT -4) are all on summer
        time while Beijing never changes -- the whole point of the demo table.
        """
        result = planner.plan(demo_roster(), WEEK_B, 60)
        best = result.best
        self.assertEqual(best.score, 235)
        self.assertEqual(best.count, 4)
        self.assertEqual(best.start.strftime("%Y-%m-%d %H:%M"), "2026-04-06 11:00")
        self.assertGreater(best.continuous_min,
                           planner.plan(demo_roster(), WEEK_A, 60).best.continuous_min)


class TestExport(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="teamsync_test_")
        self.plan = planner.plan(demo_roster(), WEEK_A, 60)

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_export_files_written(self):
        for kind in ("txt", "ics", "html"):
            path = storage.export(self.plan, "Test Team", kind, folder=self.folder)
            self.assertTrue(os.path.isfile(path), kind)
            self.assertGreater(os.path.getsize(path), 0, kind)

    def test_ics_line_folding(self):
        path = storage.export(self.plan, "Test Team", "ics", folder=self.folder)
        with open(path, "rb") as handle:
            raw = handle.read()
        for line in raw.split(b"\r\n"):
            self.assertLessEqual(len(line), 75)

    def test_ics_crlf_clean(self):
        path = storage.export(self.plan, "Test Team", "ics", folder=self.folder)
        with open(path, "rb") as handle:
            raw = handle.read()
        self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"))
        self.assertNotIn(b"\r\r", raw)

    def test_latest_json_matches_memory(self):
        path = os.path.join(self.folder, "latest.json")
        storage.save_json(path, self.plan, "Test Team")
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        top = data["recommendations"][0]
        self.assertEqual(top["score"], self.plan.best.score)
        self.assertEqual(top["cover"], self.plan.best.count)
        self.assertEqual(top["continuous_h"],
                         round(self.plan.best.continuous_hours, 1))
        self.assertEqual([item["name"] for item in top["members"]],
                         [item.name for item in self.plan.best.members])


@unittest.skipUnless(HAS_TK, "tkinter is not available")
class TestGuiSmoke(unittest.TestCase):
    """One Tk root per class: several roots in one process upset ttk."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tkinter.Tk()
        except tkinter.TclError as exc:                # no display at all
            raise unittest.SkipTest("no display: %s" % exc)
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        for child in list(self.root.winfo_children()):
            child.destroy()

    def tearDown(self):
        for child in list(self.root.winfo_children()):
            child.destroy()

    def test_window_builds(self):
        import gui

        app = gui.TeamSyncApp(self.root, DEMO_TEAM)
        self.root.update_idletasks()
        self.assertEqual(sorted(app.pages), ["grid", "members", "results"])
        self.assertEqual(len(app.members.get_children()), 4)
        self.assertEqual(len(app.members["columns"]), 7)

    def test_compute_fills_the_views(self):
        import gui

        app = gui.TeamSyncApp(self.root, DEMO_TEAM)
        app.auto_save.set(False)                       # keep the project folder clean
        result = app.compute()
        self.root.update_idletasks()
        self.assertIsNotNone(result)
        self.assertTrue(app.status.get().startswith("3 recommendation"),
                        app.status.get())
        self.assertEqual(len(app.localview.get_children()), 4)
        self.assertGreater(len(app.heat.find_all()), 0)
        self.assertGreater(len(app.results_view.inner.winfo_children()), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
