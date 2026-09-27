"""Rendered-line tests for the render-zone seam and tiebreak (b), path (a).

Ruled 2026-09-27 (label vs convert; seam-report rulings): a group's
render_zone / end_render_zone reach the page only through render_coming_up.
A zoned calendar item CONVERTS into the render zones - all or nothing when it
has an end; a gmail appointment's bare clock is LABELED in render_zone;
_time_label's convention is untouched (home bare, non-home labeled, cross-zone
ranges label both); a null or unresolvable key keeps the label as built.
Tiebreak (b): inside a synthesized group, an unzoned primary yields to the one
zoned member whose start, in render_zone, has the primary's clock.

fixtures/render_zone_fixtures.json is self-authored in the events-and-state
shape, plus a grouping in the shape run_digest passes to build_digest. Each
case's expected day header and line were PINNED FROM A CAPTURED RUN, never
typed before it: the renderer is the specification here, and a predicted
string tests the prediction. The Q3 case pins an accepted wart as it stands
(a convert across local midnight keeps its stored date), so a change to that
behaviour fails this test and has to be ruled.

No I/O beyond reading the fixture and the provenance header. No state file,
no network, no API. Importing digest imports the Google and Anthropic client
libraries; nothing here calls them.

Run: python3 test_render_zone.py
"""

import json
import os
import socket
import subprocess
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from digest import build_digest

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "render_zone_fixtures.json")


def provenance():
    """Instruments identify themselves (working rule, 2026-09-14)."""
    try:
        head = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=HERE, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = "(git unavailable)"
    print(f"host: {socket.gethostname()}")
    print(f"pwd:  {os.getcwd()}")
    print(f"head: {head}\n")


def aware(endpoint):
    """[date, clock, zone] -> the aware datetime fetch_calendar would return."""
    day, clock, zone = endpoint
    return datetime.combine(date.fromisoformat(day), time.fromisoformat(clock),
                            tzinfo=ZoneInfo(zone))


def events_of(fixture):
    return [{"id": e["id"], "summary": e["summary"], "all_day": e["all_day"],
             "start_local": aware(e["start"]), "end_local": aware(e["end"])}
            for e in fixture["events"]]


def grouping_of(fixture):
    """The grouping as run_digest builds it. conflicts is empty on every
    group: this test is about the time label, and derive_conflicts has its
    own test."""
    groups = [{**c["group"], "container": None, "conflicts": []}
              for c in fixture["cases"]]
    return {"records": fixture["records"], "groups": groups, "cause": None}


def rendered(fixture):
    """{tag: (day_header, line)} for every case, from one build_digest call."""
    state = {"commitments": {c["id"]: c for c in fixture["commitments"]}}
    _, body = build_digest(state, events_of(fixture), fixture["today"],
                           grouping=grouping_of(fixture))
    lines = body.splitlines()
    out = {}
    for case in fixture["cases"]:
        header, hits = None, []
        for line in lines:
            if not line.startswith(" ") and line and line != "COMING UP":
                header = line
            elif case["tag"] in line:
                hits.append((header, line.strip()))
        out[case["case"]] = hits
    return out


def main():
    provenance()
    with open(FIXTURE, encoding="utf-8") as handle:
        fixture = json.load(handle)
    got = rendered(fixture)
    failures = 0
    for case in fixture["cases"]:
        name = case["case"]
        hits = got[name]
        print(name)
        if len(hits) != 1:
            failures += 1
            print(f"  FAIL — {len(hits)} lines carry {case['tag']!r}: {hits}")
            continue
        header, line = hits[0]
        want = case.get("expected")
        if want is None:
            failures += 1
            print(f"  UNPINNED — {header} | {line}")
            continue
        if [header, line] != [want["header"], want["line"]]:
            failures += 1
            print(f"  FAIL — got {header} | {line}\n         want {want['header']} | {want['line']}")
            continue
        print(f"  OK — {header} | {line}")
    if failures:
        raise SystemExit(f"\n{failures} case(s) failed or unpinned.")
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
