# -*- coding: utf-8 -*-
"""City lookup and IANA timezone loading.

Two jobs:

1. turn whatever the user typed ("Beijing", "Beijing ", "beijing", "asia/shanghai")
   into an IANA timezone key plus a display name, using ``data/cities.json``;
2. load the matching TZif file from ``data/tzfiles/`` through
   ``ZoneInfo.from_file()``.

Why ``from_file()`` instead of ``ZoneInfo("Asia/Shanghai")``?  On Windows the
standard library ships no IANA database, so ``zoneinfo.TZPATH`` is an empty tuple
and the normal constructor raises ZoneInfoNotFoundError. Reading the bundled file
works on every platform with no installation step for the marker.
"""

import json
import os
from zoneinfo import ZoneInfo

from models import Availability, Member, TeamSyncError

HERE = os.path.dirname(os.path.abspath(__file__))
CITIES_JSON = os.path.join(HERE, "data", "cities.json")
TZFILES_DIR = os.path.join(HERE, "data", "tzfiles")

_city_cache = {}
_tz_cache = {}


class UnknownCity(TeamSyncError):
    """Raised when a city name cannot be matched against data/cities.json."""


class TimezoneDataMissing(TeamSyncError):
    """Raised when a bundled TZif file is missing or unreadable."""


def _squash(text):
    """'New York' / 'new-york' / 'new_york' all become 'newyork' before comparing."""
    return "".join(ch for ch in str(text).lower() if ch not in " _-.")


def load_cities(path=CITIES_JSON):
    """Return the parsed cities.json, reading each path only once."""
    if path not in _city_cache:
        with open(path, encoding="utf-8") as handle:
            _city_cache[path] = json.load(handle)
    return _city_cache[path]


def city_names(path=CITIES_JSON):
    """Sorted list of every city TeamSync knows about."""
    return sorted(load_cities(path))


def lookup(name, path=CITIES_JSON):
    """Resolve a city name to ``(display_name, tzkey)``.

    Matching is case-insensitive and ignores spaces, dots, underscores and
    hyphens; the IANA key, the country and every alias count as a match too.
    Raises UnknownCity when nothing matches.
    """
    wanted = _squash(name)
    if wanted:
        for city, info in load_cities(path).items():
            candidates = [city, info["tzkey"], info.get("country", "")]
            candidates.extend(info.get("aliases", []))
            if any(_squash(item) == wanted for item in candidates):
                return city, info["tzkey"]
    raise UnknownCity("unknown city: %r -- try  python main.py --cities" % (name,))


def load_tz(tzkey):
    """Return a ZoneInfo for ``tzkey``, read from data/tzfiles/ and cached."""
    if tzkey not in _tz_cache:
        path = os.path.join(TZFILES_DIR, *tzkey.split("/"))
        if not os.path.isfile(path):
            raise TimezoneDataMissing(
                "no bundled timezone file for %r\n"
                "run  python tools/build_tzfiles.py  to regenerate data/tzfiles/"
                % (tzkey,))
        with open(path, "rb") as handle:
            _tz_cache[tzkey] = ZoneInfo.from_file(handle, key=tzkey)
    return _tz_cache[tzkey]


def make_member(name, city, windows=None, tzkey=None, path=CITIES_JSON,
                required=True, away=(), busy=()):
    """Build a Member bound to ``city``.

    ``windows`` is a list of Availability objects or of the raw dictionaries used
    by demo_team.json; ``away`` is a collection of ``datetime.date`` objects and
    ``busy`` a collection of imported ``BusyBlock`` objects.
    The timezone file is loaded straight away so a missing data file fails here,
    with a clear message, instead of in the middle of a scan.
    """
    display, resolved = lookup(city, path)
    resolved = tzkey or resolved
    load_tz(resolved)
    parsed = [Availability.from_dict(item) if isinstance(item, dict) else item
              for item in (windows or [])]
    return Member(name, display, resolved, parsed,
                  required=required, away=away, busy=busy)
