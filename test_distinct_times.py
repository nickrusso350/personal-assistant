"""Unit tests for synthesize._distinct_times, through both of its consumers.

Ruled 2026-09-14, shipped in 1cc90a4: a time conflict is a disagreement about
the instant, not the clock string. Zoned members compare as UTC instants; a
zone-null (gmail) member agrees when its clock matches any zoned member's
clock and otherwise counts as distinct. _distinct_times is the single helper
behind derive_conflicts and filter_time_conflict, so every case asserts the
helper's count AND both consumers' output - they cannot drift apart unseen.

Cases a-d are the ones 1cc90a4 was verified against ("synthetic fixture
(4 cases)"). That script was not retained; these records are rebuilt from the
case descriptions recorded at the ruling. The date is fixed, not relative to
today, because the offsets under test depend on it: on 2026-09-14 New York and
Chicago are both on daylight time, so 19:30 in one is 18:30 in the other.

Cases e-h cover the 2026-10-11 ruling: a zoned member also presents its clock
converted to the process-local zone, so a zone-null clock matching that
presentation agrees. The helper reads the local zone from the interpreter
(astimezone() with no argument), so main() pins it - TZ=America/New_York and
time.tzset(), restored in a finally - for every case, a-d included. The result
does not depend on the machine the test runs on.

No I/O beyond the provenance header, no network, no state file access.
Importing synthesize imports the anthropic SDK; nothing here calls it.

Run: python3 test_distinct_times.py
"""

import os
import socket
import subprocess
import time

from synthesize import _distinct_times, derive_conflicts, filter_time_conflict

DAY = "2026-09-14"
NY = "America/New_York"
CHI = "America/Chicago"
UTC = "UTC"
PROCESS_LOCAL = NY   # pinned in main(); 17:00 UTC is 13:00 EDT on DAY


def provenance():
    """Instruments identify themselves (working rule, 2026-09-14)."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        head = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=here, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = "(git unavailable)"
    print(f"host: {socket.gethostname()}")
    print(f"pwd:  {os.getcwd()}")
    print(f"head: {head}\n")


def record(clock, zone):
    """The three fields _distinct_times reads, in the shape build_records emits."""
    return {"date": DAY, "time": clock, "zone": zone}


def check(by_id, expected_count):
    """Assert the helper's count, then that both consumers agree with it."""
    members = list(by_id)
    timed = [m for m in members if by_id[m].get("time")]

    count = _distinct_times(timed, by_id)
    assert count == expected_count, (count, expected_count)

    derived = derive_conflicts(members, by_id)
    filtered = filter_time_conflict({"field": "time", "members": members}, by_id)
    if expected_count >= 2:
        expected = {"field": "time", "members": timed}
        assert derived == [expected], derived
        assert filtered == expected, filtered
    else:
        assert derived == [], derived
        assert filtered is None, filtered


def test_a_same_instant_two_zones_plus_matching_bare_clock():
    """One flight stored by two writers in two zones, plus a gmail commitment
    carrying the departure clock and no zone: one instant, no conflict. This
    is the 9/14 false positive the ruling retires."""
    by_id = {
        "r1": record("19:30", NY),
        "r2": record("18:30", CHI),
        "r3": record("19:30", None),
    }
    clocks = {r["time"] for r in by_id.values()}
    assert len(clocks) == 2, "fixture must disagree as clock strings, or it proves nothing"

    check(by_id, 1)
    print("  OK — a: 19:30 ET / 18:30 CT / bare 19:30 -> one instant, no conflict")


def test_b_real_disagreement_in_one_zone():
    """Two different instants in the same zone are a real conflict."""
    by_id = {
        "r1": record("19:30", NY),
        "r2": record("20:15", NY),
    }
    check(by_id, 2)
    print("  OK — b: 19:30 ET / 20:15 ET -> two instants, conflict on both")


def test_c_bare_clock_matching_nothing():
    """Case a with only the bare clock moved: it matches neither zoned clock,
    so it counts as its own time. A zone-null clock is unknown, never assumed
    to be local."""
    by_id = {
        "r1": record("19:30", NY),
        "r2": record("18:30", CHI),
        "r3": record("21:00", None),
    }
    check(by_id, 2)
    print("  OK — c: 19:30 ET / 18:30 CT / bare 21:00 -> two times, conflict")


def test_d_all_day_only():
    """All-day records carry no time and cannot disagree about one (ruled
    2026-09-04): nothing is counted, nothing is derived, the filter drops it."""
    by_id = {
        "r1": record(None, None),
        "r2": record(None, None),
    }
    check(by_id, 0)
    print("  OK — d: all-day only -> no times counted, no conflict")


def test_e_bare_clock_matches_process_local_presentation():
    """The 2026-10-10 specimen's agreeing pair: a calendar event stored as
    17:00 UTC and a gmail 13:00 with no zone. 17:00 UTC is 13:00 in the pinned
    process-local zone, so they are one time (ruled 2026-10-11)."""
    by_id = {
        "r4": record("17:00", UTC),
        "r6": record("13:00", None),
    }
    check(by_id, 1)
    print("  OK — e: 17:00 UTC / bare 13:00, local ET -> one time, no conflict")


def test_f_bare_clock_matches_neither_presentation():
    """The specimen's disagreeing pair: 08:30 is neither 17:00 (stored) nor
    13:00 (process-local), so it stays its own time."""
    by_id = {
        "r4": record("17:00", UTC),
        "r8": record("08:30", None),
    }
    check(by_id, 2)
    print("  OK — f: 17:00 UTC / bare 08:30, local ET -> two times, conflict")


def test_g_stored_zone_is_process_local():
    """Stored zone equal to the process-local zone: both presentations are
    13:00, and the bare 13:00 agrees as it did before the ruling."""
    by_id = {
        "r1": record("13:00", NY),
        "r2": record("13:00", None),
    }
    check(by_id, 1)
    print("  OK — g: 13:00 ET / bare 13:00, local ET -> one time, no conflict")


def test_h_bare_clocks_only():
    """Zone-null against zone-null is unchanged: string comparison."""
    check({"r1": record("13:00", None), "r2": record("13:00", None)}, 1)
    print("  OK — h: bare 13:00 / bare 13:00 -> one time, no conflict")
    check({"r1": record("13:00", None), "r2": record("13:30", None)}, 2)
    print("  OK — h: bare 13:00 / bare 13:30 -> two times, conflict")


def main():
    provenance()
    tests = [
        ("a. same instant, two zones, matching bare clock", test_a_same_instant_two_zones_plus_matching_bare_clock),
        ("b. real disagreement in one zone", test_b_real_disagreement_in_one_zone),
        ("c. bare clock matching nothing", test_c_bare_clock_matching_nothing),
        ("d. all-day only", test_d_all_day_only),
        ("e. bare clock matches the process-local presentation", test_e_bare_clock_matches_process_local_presentation),
        ("f. bare clock matches neither presentation", test_f_bare_clock_matches_neither_presentation),
        ("g. stored zone is the process-local zone", test_g_stored_zone_is_process_local),
        ("h. bare clocks only", test_h_bare_clocks_only),
    ]
    saved = os.environ.get("TZ")
    os.environ["TZ"] = PROCESS_LOCAL
    time.tzset()
    try:
        for name, test in tests:
            print(name)
            test()
    finally:
        if saved is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = saved
        time.tzset()
    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
