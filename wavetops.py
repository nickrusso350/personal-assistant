"""Wave-tops: the weekly reader of the persisted grouping (ruled 2026-10-30,
selection rule ruled 2026-11-07). Not on the 07:00 path: nothing the
scheduled run executes imports this module.

The 10/30 rule (Nick, Q2/Q3): In - travel legs; timed events with a
non-empty place; all-day events with a place. Out - tasks by kind; place-less
events. Hard cap five. Every travel leg first, then remaining by duration
descending, ties chronological. Zero vital -> no video, logged. No judgment
clause; trigger for adding one is a real crowding-out morning.

Ruled 2026-11-07 against the persisted grouping shape
(groupings/grouping_YYYY-MM-DD.json, written by digest.write_grouping):
- Unit: the group. Tests run on the group's primary record; a group is
  travel if any member is; select() returns (chosen, evidence), both lists
  of groups (return shape ruled 2026-11-11; see select).
- Travel: a FLIGHT <n> identifier on any member (one is enough;
  _is_journey is the renderer's two-flight test and is not used here), or
  synthesize.codes_only(primary) returning a pair. Amended 2026-11-08/10/11:
  the non-null-container clause is out and route_pair on any member is in
  (see _is_travel). There is no Travel calendar (ruled 2026-11-10).
- Place: location != "". A gmail or iMessage record always carries "", so it
  can be selected only as a travel leg.
- All-day: source == "calendar" and kind == "event" and time is None.
- Duration: timed = end - start from time/zone and end_time/end_zone,
  end-before-start read as next day (> 24 h timed is an accepted wart).
  All-day = remaining span from the run date, end_date - run_date + 1 days,
  or 1 when end_date is null. Travel groups order chronologically among
  themselves; duration never ranks them.
- Window: primary date in [run_date, run_date + 6]; null date out.
- Fallback morning (groups null): empty selection, one logged line carrying
  cause.
- Log lines: WAVETOPS SELECT <k> of <n> groups; on zero,
  WAVETOPS SELECT 0 of <n> groups: <reason>.

Pure: no file or network I/O, no model call, deterministic. The one side
effect is the single WAVETOPS SELECT line, printed by select() on every call.

Ruled 2026-11-07 (Section 2 review):
- (1) TASK_KINDS is ("todo", "attention"); "appointment" and "event" are
  not tasks.
- (2) Kind beats everything: the task test runs first, on the primary; the
  travel test second, on any member. A todo primary carrying a FLIGHT
  identifier is out.
- (3) Duration is compared in minutes across timed and all-day groups, so a
  one-day all-day span (1440) outranks any timed event under 24 h. Intended.
- (4) Chronological order: primary date; all-day first on its date; zoned
  instants where time and zone are present, bare clock otherwise; file
  order last.
- (5) A fallback morning logs n as 0 (there are no groups to count) and the
  reason as "fallback: <cause>"; zero vital logs "no vital groups".

- (4a) On one date, zoned primaries sort before bare-clock primaries; a bare
  clock is never read in the home zone for ordering (the 9/27 label-not-
  convert line, applied by shape). A total order, so the sort stays
  deterministic and transitive; a pairwise "instants when both zoned, clocks
  otherwise" comparison can cycle (10:00 New York < 08:00 Los Angeles by
  instant, while a bare 09:00 sits between them by clock). Recorded wart: a
  bare 06:00 leg sorts after a zoned 23:00 one on the same date. Reopen
  trigger: two same-day legs reading out of order.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from synthesize import codes_only, route_pair

CAP = 5
WINDOW_DAYS = 7          # run_date through run_date + 6
TASK_KINDS = ("todo", "attention")
MINUTES_PER_DAY = 24 * 60


def _is_travel(group, by_id):
    """Any member carries a FLIGHT identifier, codes_only(primary) returns a
    pair, or any member's route_pair is not None.

    Container clause removed (ruled OUT 2026-11-08): over 46 backfill
    mornings it was the only signal that fired on a non-flight group, fired
    on 3 of 7 mornings over identical records, and swept in an errand; the
    model emits the container inconsistently, so it cannot carry rank.
    route_pair on any member added (ruled 2026-11-10): a title naming
    exactly one airport-code run is a leg, whatever its location - the
    calendar leg that names its airport by street address is the case
    codes_only refuses. codes_only(primary) is kept as written; any primary
    it admits, route_pair admits too, so it decides nothing new.
    Same commit as the lodging clause (ruled 2026-11-11): 67 groups passed
    this test on the container alone, hotels among them, and without
    _is_lodging beside the removal a hotel would fall into the placed
    bucket and speak.
    """
    for m in group["members"]:
        if any(i.startswith("FLIGHT ") for i in by_id[m].get("identifiers") or []):
            return True
    if codes_only(by_id[group["primary"]]) is not None:
        return True
    return any(route_pair(by_id[m]) is not None for m in group["members"])


def _is_task(primary):
    """The primary's kind is a task kind."""
    return primary.get("kind") in TASK_KINDS


def _has_place(primary):
    """The primary's location is non-empty."""
    return primary.get("location", "") != ""


def _is_all_day(primary):
    return (primary.get("source") == "calendar" and primary.get("kind") == "event"
            and primary.get("time") is None)


def _is_lodging(record):
    """An all-day placed calendar event: trip evidence that never speaks.

    Ruled 2026-11-11, option (a): any record with _is_all_day and a
    non-empty location, multi-day or single-day. Record-level, so the
    no-flight sentence source can collect the evidence over every record in
    a file whatever its group role; select() applies it to the primary.
    Basis: the spanning hotel event leaves the file once its start passes,
    so mid-stay only the single-day check-out twin remains, and a test that
    needed the span and the twin together would let the twin speak. Over 58
    files, 21/21 multi-day rows of this shape are hotels.
    Cost on record: an all-day placed event Nick writes himself goes silent;
    0 such events in 58 files. Reopen trigger: the first all-day placed
    event on the digest that is not a hotel; first candidate fix a fixed
    generic token list on the title (hotel/inn/suites/resort/lodge).
    """
    return _is_all_day(record) and record.get("location", "") != ""


def _clock(text):
    hours, minutes = text.split(":")
    return int(hours), int(minutes)


def _duration_minutes(primary, run_date):
    """Timed: end - start, end-before-start read as next day. All-day: the
    remaining span from run_date in days, as minutes. No end: 0."""
    # All-day branch (the 11/07 remaining-span rule, kept 2026-11-11):
    # unreachable from select() while the 11/11 option-(a) lodging clause
    # stands - an all-day group with no place fails the place test, and one
    # with a place is lodging. Kept as the landing pad for the lodging reopen
    # fix (title token list); removal is a backlog line.
    if _is_all_day(primary):
        end_date = primary.get("end_date")
        days = (date.fromisoformat(end_date) - run_date).days + 1 if end_date else 1
        return days * MINUTES_PER_DAY
    if primary.get("time") is None or primary.get("end_time") is None:
        return 0
    day = date.fromisoformat(primary["date"])
    zone, end_zone = primary.get("zone"), primary.get("end_zone")
    start_tz = ZoneInfo(zone) if zone and end_zone else None
    end_tz = ZoneInfo(end_zone) if zone and end_zone else None
    start = datetime(day.year, day.month, day.day, *_clock(primary["time"]), tzinfo=start_tz)
    end = datetime(day.year, day.month, day.day, *_clock(primary["end_time"]), tzinfo=end_tz)
    if end < start:
        nxt = day + timedelta(days=1)
        end = datetime(nxt.year, nxt.month, nxt.day, *_clock(primary["end_time"]), tzinfo=end_tz)
    return int((end - start).total_seconds() // 60)


def _chrono_key(primary):
    """(date, all-day first, zoned before bare, instant or clock). A zoned
    timed primary sorts by its UTC instant (seconds); a bare one by its clock
    string. File order is the caller's last term."""
    clock = primary.get("time")
    if clock is None:
        return (primary["date"], 0, 0, 0)
    zone = primary.get("zone")
    if zone:
        day = date.fromisoformat(primary["date"])
        instant = datetime(day.year, day.month, day.day, *_clock(clock),
                           tzinfo=ZoneInfo(zone))
        return (primary["date"], 1, 0, instant.timestamp())
    return (primary["date"], 1, 1, clock)


def _log(k, n, reason=None):
    line = f"WAVETOPS SELECT {k} of {n} groups"
    print(line if reason is None else f"{line}: {reason}")


def select(grouping, run_date):
    """The week's vital groups from one persisted grouping, at most CAP,
    travel first (chronological), then the rest by duration descending, ties
    chronological. Prints one WAVETOPS SELECT line.

    Returns (chosen, evidence), both lists of group dicts (shape ruled
    2026-11-11): chosen is the selection; evidence holds the in-window,
    non-task groups whose primary is _is_lodging, in file order - trip
    evidence that never speaks and never counts toward CAP. A fallback
    morning returns ([], [])."""
    groups = grouping.get("groups")
    if groups is None:
        _log(0, 0, f"fallback: {grouping.get('cause')}")
        return [], []
    by_id = {r["id"]: r for r in grouping.get("records") or []}
    last = run_date + timedelta(days=WINDOW_DAYS - 1)

    travel, other, evidence = [], [], []
    for index, group in enumerate(groups):
        primary = by_id[group["primary"]]
        if primary.get("date") is None:
            continue
        if not run_date <= date.fromisoformat(primary["date"]) <= last:
            continue
        if _is_task(primary):
            continue
        if _is_lodging(primary):
            # Ruled 2026-11-11: after the task test, before the travel test.
            # Evidence, never the ranked list (see _is_lodging).
            evidence.append(group)
            continue
        if _is_travel(group, by_id):
            travel.append((_chrono_key(primary), index, group))
        elif not _has_place(primary):
            continue
        else:
            other.append((-_duration_minutes(primary, run_date),
                          _chrono_key(primary), index, group))

    travel.sort(key=lambda t: t[:2])
    other.sort(key=lambda t: t[:3])
    chosen = ([g for *_, g in travel] + [g for *_, g in other])[:CAP]
    if chosen:
        _log(len(chosen), len(groups))
    else:
        _log(0, len(groups), "no vital groups")
    return chosen, evidence


def no_flight_trip(grouping, run_date, chosen):
    """(destination | None, first_date, last_date) for a week proven a trip
    by lodging alone, else None. Ruled 2026-11-11 (no-flight week).

    The evidence set is every _is_lodging record in the file - any group,
    any role - whose date falls in the selection window [run_date,
    run_date + 6] (record-level by the 11/11 3b ruling). It is wider than
    select()'s evidence groups on purpose: over 58 files, 30 groups hold a
    lodging member under a timed gmail primary with no place (the booking
    email leads the stay), and those groups reach neither the selection nor
    select()'s evidence list. Applies only when that set is non-empty and no
    chosen group is travel (travel sorts first, so any in-window travel
    group is in chosen). Dates are ISO strings from the records: first =
    min date, last = max of (end_date, or date when end_date is null).
    Destination is city_from_location over those records' locations:
    exactly one distinct non-None city is the destination; none, or more
    than one, is None (hotel-city wart accepted: the hotel's city, not the
    trip's). The caller drops the destination and logs on None; this
    function is pure and prints nothing.
    """
    by_id = {r["id"]: r for r in grouping.get("records") or []}
    last_day = run_date + timedelta(days=WINDOW_DAYS - 1)
    records = [r for r in by_id.values()
               if _is_lodging(r) and r.get("date") is not None
               and run_date <= date.fromisoformat(r["date"]) <= last_day]
    if not records:
        return None
    if any(_is_travel(g, by_id) for g in chosen):
        return None
    from destinations import city_from_location   # Step 4 module (2026-11-11)
    first = min(r["date"] for r in records)
    last = max(r.get("end_date") or r["date"] for r in records)
    cities = {city_from_location(r["location"]) for r in records} - {None}
    destination = next(iter(cities)) if len(cities) == 1 else None
    return destination, first, last


def script(selection):
    """Write the wave-tops script for a selection (not built)."""
    raise NotImplementedError


def handoff(script_text):
    """Hand the script to the video step (not built)."""
    raise NotImplementedError
