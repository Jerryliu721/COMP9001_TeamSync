# -*- coding: utf-8 -*-
"""One-off helper: copy the IANA timezone files TeamSync needs into data/tzfiles/.

Windows ships without the IANA database, so ``zoneinfo.TZPATH`` is empty there
and ``ZoneInfo("Asia/Shanghai")`` raises ZoneInfoNotFoundError. TeamSync therefore
reads the TZif files directly through ``ZoneInfo.from_file()``.

Run this once on a machine that *does* have the database (Linux / macOS, or any
machine with the ``tzdata`` pip package), then ship the generated files with the
project:

    python tools/build_tzfiles.py
"""

import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CITIES_JSON = os.path.join(ROOT, "data", "cities.json")
OUT_DIR = os.path.join(ROOT, "data", "tzfiles")


def find_source_root():
    """Return the folder holding the IANA zone tree (Asia/, Europe/, ...) or None."""
    try:
        import tzdata
    except ImportError:
        pass
    else:
        return os.path.join(os.path.dirname(tzdata.__file__), "zoneinfo")

    for candidate in ("/usr/share/zoneinfo", "/usr/lib/zoneinfo", "/etc/zoneinfo"):
        if os.path.isdir(os.path.join(candidate, "Asia")):
            return candidate
    return None


def main():
    source_root = find_source_root()
    if source_root is None:
        print("ERROR: no IANA timezone database found on this machine.")
        print("       Install it with  pip install tzdata  and run this script again.")
        return 1

    with open(CITIES_JSON, encoding="utf-8") as fh:
        cities = json.load(fh)
    keys = sorted({info["tzkey"] for info in cities.values()})

    copied, total = 0, 0
    for key in keys:
        source = os.path.join(source_root, *key.split("/"))
        target = os.path.join(OUT_DIR, *key.split("/"))
        if not os.path.isfile(source):
            print("  ! not found in source database: %s" % key)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
        copied += 1
        total += os.path.getsize(target)

    print("source database : %s" % source_root)
    print("cities listed   : %d" % len(cities))
    print("files copied    : %d  (%.1f KB total)" % (copied, total / 1024.0))
    print("destination     : %s" % OUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
