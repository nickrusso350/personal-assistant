"""Render the digest against an in-memory fixture set.

No state.json, no network, no API calls, no assertions — every fixture below
names the section it should land in, and reading the output is the
verification. All dates are relative to today, so this never goes stale.

Run: python3 preview_digest.py
"""

import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from digest import TEXT_UNAVAILABLE, build_digest, partition_commitments
from synthesize import build_records, synthesize

TODAY = date.today()


def iso(offset_days):
    """An ISO date string offset_days from today."""
    return (TODAY + timedelta(days=offset_days)).isoformat()


def commitment(cid, what, ctype, date_str, **extra):
    """A commitment in the shape process_messages builds."""
    record = {
        "id": cid,
        "status": "open",
        "first_seen": TODAY.isoformat(),
        "resolved_on": None,
        "type": ctype,
        "what": what,
        "date": date_str,
        "time": None,
        "action_needed": None,
        "sender": "someone@example.com",
        "subject": "(fixture)",
    }
    record.update(extra)
    return record


FIXTURES = [
    # 1 — dated inside the horizon -> TO DO
    commitment("f1", "Send the signed lease back", "task", iso(3)),
    # 2 — dated in the past -> NEEDS ATTENTION
    commitment("f2", "Pay the water bill", "task", iso(-1)),
    # 3 — no date at all -> TO DO, with no "due" suffix
    commitment("f3", "Book a dentist cleaning", "task", None),
    # 4 — appointment inside the horizon -> COMING UP, rendered "1:00 PM"
    commitment("f4", "Lunch with Dana", "appointment", iso(2), time="13:00"),
    # 5 — appointment beyond the horizon -> hidden until it comes into range
    commitment("f5", "Annual physical", "appointment", iso(30)),
    # 6 — appointment already past -> dropped, the day is gone
    commitment("f6", "Car inspection", "appointment", iso(-1)),
    # 7 — dateless, non-task type -> TO DO
    commitment("f7", "Reply to Marcus about the quote", "reply_needed", None),
    # 8 — unknown type falls through to the non-appointment branch -> TO DO
    commitment("f8", "Widget of unknown provenance", "widget", iso(1)),
    # 9 — resolved -> appears in no section at all
    commitment("f9", "Already handled", "task", iso(-1), status="resolved"),
    # 10 — approved appointment -> suppressed; the calendar fetch surfaces it
    commitment("f10", "Approved: parent-teacher conference", "appointment", iso(2),
               time="16:00", calendar={"status": "approved", "event_id": "evt_1"}),
    # 11 — rejected appointment -> still renders; no calendar event exists for it
    commitment("f11", "Rejected: optional team offsite", "appointment", iso(2),
               time="09:30", calendar={"status": "rejected"}),
    # 12 — text-sourced appointment (wired 2026-10-10) -> COMING UP, "7:00 PM",
    # carrying the [text] tag process_texts writes into what
    commitment("imsg:fixture-guid-1:0", "[text] Dinner with Sam", "appointment", iso(3),
               time="19:00", source="imessage", sender="them", subject=None),
]

STATE = {"commitments": {c["id"]: c for c in FIXTURES}}

# Two events in the shape fetch_upcoming_events returns: timed events carry
# datetimes, all-day events carry plain dates. The all-day one shares its date
# with fixtures 4 and 11, so COMING UP proves it interleaves by date rather
# than by source.
ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")

# Production always returns AWARE datetimes (fetch_calendar does
# .astimezone(tz)), so timed fixtures carry tzinfo or they exercise the
# wrong branch of the zone-label rule.
TIMED_START = datetime.combine(TODAY + timedelta(days=1), time(10, 0), tzinfo=ET)
D2 = TODAY + timedelta(days=2)
EVENTS = [
    {
        "id": "evt_timed",
        "summary": "Sprint review",
        "start_local": TIMED_START,
        "end_local": TIMED_START + timedelta(hours=1),
        "all_day": False,
    },
    # Home zone, split meridiem: must stay BARE.
    {
        "id": "evt_tz_home_split",
        "summary": "Zone: home split meridiem (no label)",
        "start_local": datetime.combine(D2, time(11, 0), tzinfo=ET),
        "end_local": datetime.combine(D2, time(13, 0), tzinfo=ET),
        "all_day": False,
    },
    # Foreign single zone: one trailing PDT suffix.
    {
        "id": "evt_tz_foreign",
        "summary": "Zone: Sea-Tac pickup (PDT)",
        "start_local": datetime.combine(D2, time(20, 0), tzinfo=PT),
        "end_local": datetime.combine(D2, time(21, 0), tzinfo=PT),
        "all_day": False,
    },
    # Cross-zone (D2 ruling): BOTH endpoints labeled, no meridiem collapse.
    {
        "id": "evt_tz_cross",
        "summary": "Zone: cross-zone flight (both labeled)",
        "start_local": datetime.combine(D2, time(21, 35), tzinfo=ET),
        "end_local": datetime.combine(D2, time(23, 57), tzinfo=PT),
        "all_day": False,
    },
    # UTC-stored upstream defect: label makes it visibly wrong, not silent.
    {
        "id": "evt_tz_utc",
        "summary": "Zone: UTC-stored upstream (loud)",
        "start_local": datetime.combine(D2, time(13, 0), tzinfo=UTC),
        "end_local": datetime.combine(D2, time(16, 0), tzinfo=UTC),
        "all_day": False,
    },
    {
        "id": "evt_allday",
        "summary": "Company holiday",
        "start_local": TODAY + timedelta(days=2),
        "end_local": TODAY + timedelta(days=3),
        "all_day": True,
    },
    # In-progress multi-day all-day: started 2 days ago, last day
    # TODAY+3 (end is exclusive, so end_local = TODAY+4).
    # Must render the "through" form.
    {
        "id": "evt_stay_inprogress",
        "summary": "Stay: Fixture Hotel",
        "start_local": TODAY - timedelta(days=2),
        "end_local": TODAY + timedelta(days=4),
        "all_day": True,
    },
    # Not-yet-started multi-day all-day: starts TODAY+3, last day
    # TODAY+5. Must KEEP the plain start-date form.
    {
        "id": "evt_stay_future",
        "summary": "Conference block (not started)",
        "start_local": TODAY + timedelta(days=3),
        "end_local": TODAY + timedelta(days=6),
        "all_day": True,
    },
    # Final morning of a multi-day stay: last day is TODAY
    # (end is exclusive, so end_local = TODAY+1).
    # Must render the "Last day:" form.
    {
        "id": "evt_stay_lastday",
        "summary": "Stay: Checkout Hotel",
        "start_local": TODAY - timedelta(days=2),
        "end_local": TODAY + timedelta(days=1),
        "all_day": True,
    },
]


# ---------------------------------------------------------------------------
# Duplication display-collapse corpus (ruled 2026-09-02). D4 carries the
# positive cases, D5 the negatives. A positive fixture proves the
# discriminator fires; only the negatives prove it discriminates.
D4 = TODAY + timedelta(days=4)
D5 = TODAY + timedelta(days=5)

DUP_FIXTURES = [
    # P1 — the 8/2 case pair, same time: collapses to the longer summary
    commitment("d1", "Check-in at Fixture Inn Seattle North", "appointment", iso(4), time="15:00"),
    commitment("d2", "Hotel check-in at Fixture Inn Seattle North", "appointment", iso(4), time="15:00"),
    # P2 — thread double-extraction (identical text) + parser calendar event
    # at the same start (see DUP_EVENTS): one line from three items, the
    # appointment label surviving, NOT the parser's leg-arrival range
    commitment("d3", "Delta flight TPA to SEA (via ATL) - Confirmation FIXTUR", "appointment", iso(4), time="17:55"),
    commitment("d4", "Delta flight TPA to SEA (via ATL) - Confirmation FIXTUR", "appointment", iso(4), time="17:55"),
    # P4 — punctuation/wording drift ("pick-up", "intermediate"): one line
    commitment("d5", "Alamo intermediate car rental pick-up at Fixture Airport", "appointment", iso(4), time="23:00"),
    commitment("d6", "Alamo car rental pick-up at Fixture Airport", "appointment", iso(4), time="23:00"),
    # N1 — check-out vs check-in, same hotel, different times: TWO lines
    commitment("d7", "Check-out at Fixture Inn Seattle North", "appointment", iso(5), time="11:00"),
    commitment("d8", "Check-in at Fixture Inn Seattle North", "appointment", iso(5), time="15:00"),
    # N2 — two legs, same airline, same day, different times: TWO lines
    commitment("d9", "Delta flight TPA to ATL", "appointment", iso(5), time="09:00"),
    commitment("d10", "Delta flight ATL to SEA", "appointment", iso(5), time="12:30"),
    # N3 — recurring obligation on different dates: TWO lines (D4 and D5)
    commitment("d11", "Weekly standup with the team", "appointment", iso(4), time="10:00"),
    commitment("d12", "Weekly standup with the team", "appointment", iso(5), time="10:00"),
    # N6 — same date/time, only TWO shared words ("dr patel"): TWO lines.
    # This is the threshold probe; if ANCHOR_RUN ever drops to 2 it collapses.
    commitment("d13", "Dentist visit with Dr Patel", "appointment", iso(5), time="14:00"),
    commitment("d14", "Prescription pickup for Dr Patel", "appointment", iso(5), time="14:00"),
]

DUP_EVENTS = [
    # P3 — two calendar writers, all-day, "Stay:" vs "Stay at": one line.
    # N4 lives here too: both share their date with P1 but time.min != 15:00,
    # so the stay pair and the check-in pair render as SEPARATE lines.
    {
        "id": "evt_dup_stay_parser",
        "summary": "Stay: Fixture Inn Seattle North",
        "start_local": D4,
        "end_local": D4 + timedelta(days=3),
        "all_day": True,
    },
    {
        "id": "evt_dup_stay_po",
        "summary": "Stay at Fixture Inn Seattle North",
        "start_local": D4,
        "end_local": D4 + timedelta(days=3),
        "all_day": True,
    },
    # P2 (calendar half) — parser leg with an end time, same start as d3/d4
    {
        "id": "evt_dup_flight_parser",
        "summary": "Flight: TPA to ATL",
        "start_local": datetime.combine(D4, time(17, 55), tzinfo=ET),
        "end_local": datetime.combine(D4, time(19, 40), tzinfo=ET),
        "all_day": False,
    },
    # N5 (8/16) — a plain-form stay starting today beside the in-progress
    # "Now-" stay (evt_stay_inprogress): different sort dates, TWO lines.
    {
        "id": "evt_dup_stay_today",
        "summary": "Stay at Fixture Hotel",
        "start_local": TODAY,
        "end_local": TODAY + timedelta(days=4),
        "all_day": True,
    },
]

FIXTURES.extend(DUP_FIXTURES)
STATE["commitments"].update({c["id"]: c for c in DUP_FIXTURES})
EVENTS.extend(DUP_EVENTS)


# ---------------------------------------------------------------------------
# Fixture 1 — the synthesis corpus (added 2026-09-04).
#
# The 9/3 live records with fake names and the real shape: thirteen source
# items that are FOUR obligations on the departure day plus one on the return
# day. Kept in its own state/event pair, never folded into the corpus above,
# because a recorded grouping partitions exactly its own records and validate()
# rejects any id it does not know.
#
# Geometry note: the real 9/3 trip ran Sep 5 -> Sep 12, eight days out. Here
# the return is iso(7) — the last day the HORIZON_DAYS window can reach — so
# the fixture exercises the return-day header. At iso(8) merge_coming_up emits
# no end item at all and the return line cannot be verified.
#
# The confirmation code is FIXTUR. The real one is never written down here.
#
# Rendering (ruled 2026-09-16): the flight group is a journey — its members
# carry two distinct FLIGHT identifiers — so it renders as two endpoint
# lines, "Departure: ..." from the primary and "Arrival: ..." from the member
# whose end instant is latest, with the sources-disagree text suppressed. The
# check-in and car groups carry no flight identifiers, are not journeys, and
# render exactly as they did.
F1_DEPART = TODAY + timedelta(days=1)
F1_RETURN = TODAY + timedelta(days=7)

HOTEL = "Fixture Suites Seattle"
SEATAC = "Seattle-Tacoma Sea-Tac International Airport"

F1_COMMITMENTS = [
    # Hotel check-in, extracted twice from one thread — identical text.
    commitment("f1_checkin_a", f"Hotel check-in at {HOTEL}", "appointment",
               iso(1), time="15:00"),
    commitment("f1_checkin_b", f"Hotel check-in at {HOTEL}", "appointment",
               iso(1), time="15:00"),
    # Whole-itinerary line, extracted three times — identical text. Joins the
    # FIRST leg's group (prompt rule 3), never its own line.
    commitment("f1_itin_a", "Delta flight TPA to SEA (via ATL) - Confirmation FIXTUR",
               "appointment", iso(1), time="17:55"),
    commitment("f1_itin_b", "Delta flight TPA to SEA (via ATL) - Confirmation FIXTUR",
               "appointment", iso(1), time="17:55"),
    commitment("f1_itin_c", "Delta flight TPA to SEA (via ATL) - Confirmation FIXTUR",
               "appointment", iso(1), time="17:55"),
    # Per-leg lines from the same mail.
    commitment("f1_leg1", "Delta flight 759: Tampa (TPA) to Atlanta (ATL)",
               "appointment", iso(1), time="17:55"),
    commitment("f1_leg2", "Delta flight 903: Atlanta (ATL) to Seattle (SEA)",
               "appointment", iso(1), time="21:35"),
    # Car pickup, extracted twice with wording drift.
    commitment("f1_car_a", f"Alamo intermediate car rental pick-up at {SEATAC}",
               "appointment", iso(1), time="23:00"),
    commitment("f1_car_b", f"Alamo car rental pick-up at {SEATAC}",
               "appointment", iso(1), time="23:00"),
]

F1_STATE = {"commitments": {c["id"]: c for c in F1_COMMITMENTS}}

F1_EVENTS = [
    # All-day stay: end is EXCLUSIVE, so last day is F1_RETURN. Yields TWO
    # records (start, end) — S2 endpoints, S1 one date per group.
    {
        "id": "f1_evt_stay",
        "summary": f"Stay: {HOTEL}",
        "start_local": F1_DEPART,
        "end_local": F1_RETURN + timedelta(days=1),
        "all_day": True,
        "location": "Seattle, WA",
        "description": "Confirmation FIXTUR",
    },
    {
        "id": "f1_evt_leg1",
        "summary": "Flight: TPA to ATL",
        "start_local": datetime.combine(F1_DEPART, time(17, 55), tzinfo=ET),
        "end_local": datetime.combine(F1_DEPART, time(19, 40), tzinfo=ET),
        "all_day": False,
        "location": "Tampa International Airport",
        "description": "Delta 759. Confirmation FIXTUR",
    },
    {
        "id": "f1_evt_leg2",
        "summary": "Flight: ATL to SEA",
        "start_local": datetime.combine(F1_DEPART, time(21, 35), tzinfo=ET),
        "end_local": datetime.combine(F1_DEPART, time(23, 57), tzinfo=PT),
        "all_day": False,
        "location": "Seattle, WA",
        "description": "Delta 903. Confirmation FIXTUR",
    },
    # The CORRECTED instant (ruled 2026-09-04): stored in Pacific, renders
    # 11:00 PM PDT. The uncorrected version was an upstream data defect, not a
    # renderer one, and is not reproduced here.
    {
        "id": "f1_evt_car",
        "summary": "Car rental pick-up: Alamo",
        "start_local": datetime.combine(F1_DEPART, time(23, 0), tzinfo=PT),
        "end_local": datetime.combine(F1_DEPART + timedelta(days=1), time(0, 0), tzinfo=PT),
        "all_day": False,
        "location": f"{SEATAC}, Seattle, WA",
        "description": "Alamo. Intermediate.",
    },
]

GROUPING_FIXTURE = Path(__file__).with_name("fixtures") / "grouping_fixture1.json"


def f1_partition():
    """Fixture 1's partition, computed the way run_digest computes it."""
    return partition_commitments(F1_STATE["commitments"], TODAY)


def record_live_grouping():
    """The ONLY networked path in this file, and only under --live.

    Builds fixture 1's records, calls synthesize once, and writes
    {records, groups, cause} to fixtures/grouping_fixture1.json so every later
    default run replays it at $0. Returns the grouping dict.

    log=False (ruled 2026-09-17, built 2026-09-21): this path prints to a
    terminal, and the SYNTHESIS lines carry unmasked record text. The recorded
    fixture is the evidence a preview run needs; the log line is not.
    """
    attention, todo, appointments = f1_partition()
    records = build_records(F1_EVENTS, attention, todo, appointments, TODAY)
    groups, cause = synthesize(records, TODAY, log=False)
    grouping = {
        "records": records,
        "groups": groups["groups"] if groups is not None else None,
        "cause": cause,
    }
    GROUPING_FIXTURE.parent.mkdir(exist_ok=True)
    with open(GROUPING_FIXTURE, "w", encoding="utf-8") as handle:
        json.dump(grouping, handle, indent=1)
    return grouping


def load_recorded_grouping():
    """The recorded grouping, or None when nothing has been recorded yet.

    None makes build_digest render the fallback with cause "off" — the honest
    state of a machine that has never paid for a synthesis call. ref tuples
    come back from JSON as lists; _index_grouping re-tuples them.
    """
    if not GROUPING_FIXTURE.exists():
        return None
    with open(GROUPING_FIXTURE, encoding="utf-8") as handle:
        return json.load(handle)


def main():
    live = "--live" in sys.argv[1:]
    today = TODAY.isoformat()

    print("=== corpus (fallback path — synthesis off) ===\n")
    title, full_body = build_digest(STATE, EVENTS, today)
    print(title + "\n\n" + full_body)
    print()
    title, compact_body = build_digest(STATE, EVENTS, today, compact_calendar=True)
    print(title + "\n\n" + compact_body)

    print("\n=== corpus, text source unavailable (notes set) ===\n")
    title, noted_body = build_digest(STATE, EVENTS, today, notes=(TEXT_UNAVAILABLE,))
    print(title + "\n\n" + noted_body)

    if live:
        print("\n=== fixture 1 (LIVE — one synthesis API call) ===\n")
        grouping = record_live_grouping()
        print(f"recorded -> {GROUPING_FIXTURE} (cause={grouping['cause']})\n")
    else:
        grouping = load_recorded_grouping()
        state = "replay" if grouping else "no recording yet"
        print(f"\n=== fixture 1 ($0 — {state}) ===\n")

    title, f1_body = build_digest(F1_STATE, F1_EVENTS, today,
                                  partition=f1_partition(), grouping=grouping)
    print(title + "\n\n" + f1_body)


if __name__ == "__main__":
    main()
