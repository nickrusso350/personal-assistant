#!/usr/bin/env python3
"""Run destinations.city_from_location and spoken_destination over
fixtures/destinations_fixtures.json.

Written 2026-11-11 for the video-side destinations module (spoken
destinations ruled 2026-11-10; city tail ruled and widened 2026-11-11).
Self-authored fixtures only; reads the repo's snapshot and overrides files,
never the network, never state, writes nothing.

Prints, per case, the result and - for destination cases - the captured
DESTINATION UNKNOWN line(s), plus an md5 over the result lines. Each case's
"expected" is checked exactly; a mismatch prints FAIL, and a case with no
"expected" prints UNPINNED and counts as a failure, so a case added without
a pin cannot pass silently. Any failure exits non-zero. Destination cases
also run with log=False and must print nothing.

Usage: python3 test_destinations.py
"""
import contextlib
import hashlib
import io
import json
import os
import socket
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

import destinations                                             # noqa: E402

FIXTURE = os.path.join(REPO, "fixtures", "destinations_fixtures.json")


def provenance():
    """Instruments identify themselves (working rule, 2026-09-14)."""
    try:
        head = subprocess.run(["git", "log", "-1", "--oneline"], cwd=REPO,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = "(git unavailable)"
    print(f"host: {socket.gethostname()}")
    print(f"pwd:  {os.getcwd()}")
    print(f"head: {head}\n")


def run_city(case):
    return {"city": destinations.city_from_location(case["location"])}


def run_destination(case):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        name = destinations.spoken_destination(case["code"])
    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet):
        quiet_name = destinations.spoken_destination(case["code"], log=False)
    if quiet.getvalue() or quiet_name != name:
        raise SystemExit(f"{case['name']}: log=False printed or changed the result")
    return {"name": name, "log": out.getvalue().splitlines()}


def main():
    provenance()
    doc = json.load(open(FIXTURE, encoding="utf-8"))
    results, failures = [], 0
    for kind, cases, fn in (("city", doc["city_cases"], run_city),
                            ("destination", doc["destination_cases"], run_destination)):
        for case in cases:
            got = fn(case)
            result = f"{kind} {case['name']}: {json.dumps(got)}"
            results.append(result)
            want = case.get("expected")
            if want is None:
                status = "UNPINNED"
                failures += 1
            elif want == got:
                status = "PASS"
            else:
                status = "FAIL"
                failures += 1
            print(f"{status} {result}")
    print(f"\nresult md5: {hashlib.md5(chr(10).join(results).encode()).hexdigest()}")
    if failures:
        raise SystemExit(f"{failures} case(s) failed.")


if __name__ == "__main__":
    main()
