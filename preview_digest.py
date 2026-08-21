"""Render the digest against an in-memory fixture set.

No state.json, no network, no API calls, no assertions — every fixture below
names the section it should land in, and reading the output is the
verification. All dates are relative to today, so this never goes stale.

Run: python3 preview_digest.py
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from digest import build_digest

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


def main():
    today = TODAY.isoformat()
    title, full_body = build_digest(STATE, EVENTS, today)
    print(title + "\n\n" + full_body)
    print()
    title, compact_body = build_digest(STATE, EVENTS, today, compact_calendar=True)
    print(title + "\n\n" + compact_body)


if __name__ == "__main__":
    main()
