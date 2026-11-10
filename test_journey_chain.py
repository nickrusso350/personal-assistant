"""Journey route-chain clause: both journey tests, and the rendered lines.

Ruled 2026-11-09. A group whose legs are named "XXX to YYY" and connect in
start order is a journey even with no flight number - the shape a phone-side
calendar entry takes. synthesize.route_chain is the one predicate; both
synthesize._is_journey (selector term 0) and digest._is_journey (Departure /
Arrival expansion, render-zone seam skip, conflict-note suppression) call it.

fixtures/journey_chain_fixtures.json is self-authored: invented reservation
codes, flight numbers and street addresses. Records are built from the
fixture events by the real build_records, the grouping carries the real
derive_conflicts output and the real _select_primaries pick, and the model's
proposed primary is set to the LAST member so the selector has work to do.

Graded per case: the expected journey value, and (h) that both copies of the
journey test agree. Cases marked "render" also print their COMING UP lines;
where "expected" is pinned the lines must match byte for byte. Expected
lines are PINNED FROM A CAPTURED RUN, never typed before it. A case marked
"ungraded" is reported and counts toward nothing.

No state file, no network, no API. Importing digest imports the Google and
Anthropic client libraries; nothing here calls them.

Run: python3 test_journey_chain.py
"""

import json
import os
import socket
import subprocess
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import digest
import synthesize

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "journey_chain_fixtures.json")


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


def events_of(case):
    return [{"id": e["id"], "summary": e["summary"], "all_day": False,
             "start_local": aware(e["start"]), "end_local": aware(e["end"]),
             "location": e["location"], "description": e["description"]}
            for e in case.get("events", [])]


def grouping_of(case, today):
    """One group over every record, as run_digest would hold it."""
    commitments = case.get("commitments", [])
    records = synthesize.build_records(events_of(case), [], [], commitments,
                                       today)
    by_id = {r["id"]: r for r in records}
    members = [r["id"] for r in records]
    group = {"members": members, "primary": members[-1], "container": None,
             "conflicts": synthesize.derive_conflicts(members, by_id),
             "render_zone": case.get("render_zone"),
             "end_render_zone": case.get("end_render_zone")}
    grouping = {"records": records, "groups": [group], "cause": None}
    synthesize._select_primaries(grouping, records, log=False)
    return grouping


def coming_up(case, grouping, today):
    """The COMING UP block for one case, from one build_digest call."""
    state = {"commitments": {c["id"]: c for c in case.get("commitments", [])}}
    _, body = digest.build_digest(state, events_of(case), today.isoformat(),
                                  grouping=grouping)
    lines = body.splitlines()
    if "COMING UP" not in lines:
        return []
    block = lines[lines.index("COMING UP") + 1:]
    end = next((i for i, line in enumerate(block) if line == ""), len(block))
    return block[:end]


def main():
    provenance()
    with open(FIXTURE, encoding="utf-8") as handle:
        fixture = json.load(handle)
    today = date.fromisoformat(fixture["today"])
    failures = passes = 0
    for case in fixture["cases"]:
        grouping = grouping_of(case, today)
        group = grouping["groups"][0]
        by_id = {r["id"]: r for r in grouping["records"]}
        s_val = synthesize._is_journey(group["members"], by_id)
        d_val = digest._is_journey(group, by_id)
        ungraded = case.get("ungraded", False)
        print(case["case"])
        print(f"  synthesize._is_journey={s_val} digest._is_journey={d_val}"
              f" route_chain={synthesize.route_chain(group['members'], by_id)}"
              f" primary={group['primary']} conflicts={bool(group['conflicts'])}")
        verdicts = []
        if s_val != d_val:
            verdicts.append("FAIL (h) - the two journey tests disagree")
        if not ungraded and s_val != case["journey"]:
            verdicts.append(f"FAIL - journey {s_val}, want {case['journey']}")
        if case.get("render"):
            got = coming_up(case, grouping, today)
            for line in got:
                print(f"    | {line}")
            want = case.get("expected")
            if not ungraded:
                if want is None:
                    verdicts.append("UNPINNED - lines above are the capture")
                elif got != want:
                    verdicts.append(f"FAIL - lines differ; want {want}")
        if ungraded:
            print("  UNGRADED")
            continue
        if verdicts:
            failures += 1
            for v in verdicts:
                print(f"  {v}")
        else:
            passes += 1
            print("  PASS")
    print(f"\n{passes} passed, {failures} failed or unpinned.")
    if failures:
        raise SystemExit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
