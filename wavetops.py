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

select() and no_flight_trip() are pure: no file or network I/O, no model
call, deterministic. select()'s one side effect is the single WAVETOPS
SELECT line, printed on every call.

The script (ruled 2026-11-23): code writes every sentence from fixed
patterns; there is no model call. fact_sheet() turns one select() call and
one no_flight_trip() call into one row of facts per spoken item, and
script() words the rows. One sentence per journey - weekday, departure
time, final destination; arrival is never spoken. The clock and zone mirror
the page (label vs convert, 2026-09-27); the zone is spoken as a word, and
only outside Eastern. Titles are cleaned and pass through
wavetops_swaps.json, whole tokens only. select()'s ranking decides the cut;
the lines are spoken in calendar order. A week with nothing chosen and no
trip is "The week ahead is clear." - this supersedes the 10/30 "zero vital
-> no video" line above. A fallback grouping has no script. The script path
reads two data files beside this module (the swap file; the destination
tables through destinations.py) and prints WAVETOPS DESTINATION DROPPED,
WAVETOPS ZONE UNKNOWN and WAVETOPS CAP DROPPED lines; it writes nothing.
handoff() is not built.

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

import json
from datetime import date, datetime, time, timedelta
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from synthesize import _is_journey, codes_only, route_chain, route_pair

CAP = 5
WINDOW_DAYS = 7          # run_date through run_date + 6
TASK_KINDS = ("todo", "attention")
MINUTES_PER_DAY = 24 * 60

# Home, as an IANA key (ruled 2026-11-23). A clock whose zone has no spoken
# word is converted here and spoken bare. A constant, because this module
# does not import digest; the other place home is set is digest's HOME_TZ
# (fetch_calendar.DEFAULT_TZ). The home zone is fixed in both.
EASTERN = "America/New_York"

# IANA key -> the zone as a spoken word (ruled 2026-11-23). An exact table,
# no scoring. The word is spoken only when it is not "Eastern": the test is
# on the word, never on the key, so a Detroit clock is spoken bare. A key
# not listed here (UTC, Phoenix, anything abroad) is converted to EASTERN,
# spoken with no word, and logged WAVETOPS ZONE UNKNOWN <key>.
# America/Phoenix is left out by ruling: "Mountain" by name, Pacific's
# offset in summer.
ZONE_WORDS = {
    "America/New_York": "Eastern",
    "America/Detroit": "Eastern",
    "America/Kentucky/Louisville": "Eastern",
    "America/Kentucky/Monticello": "Eastern",
    "America/Indiana/Indianapolis": "Eastern",
    "America/Chicago": "Central",
    "America/Denver": "Mountain",
    "America/Boise": "Mountain",
    "America/Los_Angeles": "Pacific",
    "America/Anchorage": "Alaska",
    "Pacific/Honolulu": "Hawaii",
}

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
            "Saturday", "Sunday")
SWAPS_FILE = Path(__file__).resolve().parent / "wavetops_swaps.json"
UNTITLED = "untitled event"


def _weekday(day):
    """The weekday as a word, from a date. A fixed tuple, not strftime, so
    the word never depends on the process locale."""
    return WEEKDAYS[day.weekday()]


def _spoken_clock(clock):
    """A time as spoken (ruled 2026-11-23): 12-hour, "5 PM" when the minutes
    are zero, "5:26 PM" otherwise. No rounding. Takes anything with .hour
    and .minute."""
    hour = clock.hour % 12 or 12
    meridiem = "AM" if clock.hour < 12 else "PM"
    if clock.minute == 0:
        return f"{hour} {meridiem}"
    return f"{hour}:{clock.minute:02d} {meridiem}"


@cache
def _swaps():
    """Token -> spoken replacement, loaded once from wavetops_swaps.json."""
    return json.loads(SWAPS_FILE.read_text(encoding="utf-8"))["swaps"]


def _spoken_title(summary):
    """A record's summary as the title to speak, without its closing period
    (ruled 2026-11-23). Display-only.

    Split on whitespace, which also collapses runs and newlines to single
    spaces; swap each whole token found in the swap file, byte for byte
    ("w/Name" is one token and does not swap); then strip trailing
    whitespace and . ? !. A title empty after that - "", only punctuation,
    or not a string - is UNTITLED: the item is never dropped.
    """
    if not isinstance(summary, str):
        return UNTITLED
    swaps = _swaps()
    text = " ".join(swaps.get(token, token) for token in summary.split())
    return text.rstrip(".?! ") or UNTITLED


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


def _admitted(key):
    """ZoneInfo for an IANA key, or None when it is absent or does not
    resolve. Never raises."""
    if not key or not isinstance(key, str):
        return None
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _spoken_when(group, by_id):
    """(day, clock | None, zone word | None, order) for a chosen group: the
    page's clock and zone, spoken (ruled 2026-11-23, "mirror the page").

    The clock is the primary's. A journey (synthesize._is_journey, the
    renderer's own test) speaks the primary's stored zone, as the Departure
    line does. Any other group follows the 9/27 label-vs-convert rule on the
    group's render_zone: a zoned clock CONVERTS into it - all or nothing, so
    a primary carrying an end converts only when end_render_zone admits too
    - and a bare clock is LABELED with it, digits unchanged. No admitted
    render zone: the stored zone, or a bare clock with no word.

    The zone word comes from ZONE_WORDS and is None for "Eastern" (the test
    is on the word). A key with no word is converted to EASTERN, spoken
    bare, and logged. The day is the day of the clock as spoken, so a
    convert across midnight moves the weekday with it.

    order is the tail of the calendar-order key within one spoken day (ruled
    2026-11-23, after the read): no clock first, then every clocked item by
    its spoken (hour, minute), whatever its zone. The script is heard, so
    the order is the order of the clocks as heard. This replaces the 11/07
    zoned-before-bare tail here only; select() and _chrono_key keep it.
    """
    primary = by_id[group["primary"]]
    day = date.fromisoformat(primary["date"])
    text = primary.get("time")
    if text is None:
        return day, None, None, (1, 0, 0)
    stored = _admitted(primary.get("zone"))
    render = None
    if not _is_journey(group["members"], by_id):
        render = _admitted(group.get("render_zone"))
    if stored is not None:
        moment = datetime(day.year, day.month, day.day, *_clock(text), tzinfo=stored)
        if render is not None and (
                primary.get("end_time") is None
                or _admitted(group.get("end_render_zone")) is not None):
            moment = moment.astimezone(render)
    elif render is not None:
        moment = datetime(day.year, day.month, day.day, *_clock(text), tzinfo=render)
    else:
        clock = time(*_clock(text))
        return day, clock, None, (2, clock.hour, clock.minute)
    word = ZONE_WORDS.get(moment.tzinfo.key)
    if word is None:
        print(f"WAVETOPS ZONE UNKNOWN {moment.tzinfo.key}")
        moment = moment.astimezone(ZoneInfo(EASTERN))
    return (moment.date(), moment, None if word in (None, "Eastern") else word,
            (2, moment.hour, moment.minute))


def _final_destination(group, by_id):
    """(code, None) or (None, reason) for a travel group (ruled 2026-11-23;
    chain rule amended the same day, after the read over 70 files).

    Over the members that yield a route_pair:
    - Every pair identical -> that pair's destination (twin copies of one
      leg).
    - Otherwise identical pairs are deduped to one member each - the first
      in member order that carries a start instant (date, clock and zone) -
      and those zoned legs must form a chain (synthesize.route_chain, the
      11/09 predicate, unchanged). The destination is that of the leg with
      the latest start instant.
    - A pair left over - one that only bare-clock members carry - is
      accepted only when it equals the chain's (origin, final destination):
      the booking email that names the whole itinerary end to end. A bare
      twin of a chain leg is not left over; its pair is already a leg.
      Anything else is "pairs disagree".
    - A chain that ends where it began has no destination.
    Byte comparison throughout, no scoring. The reason is one of: no route
    pair, pairs disagree, round trip.

    A zoned end-to-end pair alongside zoned legs is not accepted: it enters
    the chain test and fails it (0 instances in the read).

    Basis for the amendment: all 9 "pairs disagree" drops in the read were
    one trip - two zoned calendar legs plus bare email copies naming the
    end-to-end pair - which the first rule refused because route_chain
    needs an instant on every member.
    """
    legs = [m for m in group["members"] if route_pair(by_id[m]) is not None]
    if not legs:
        return None, "no route pair"
    pairs = [route_pair(by_id[m]) for m in legs]
    if len(set(pairs)) == 1:
        return pairs[0][1], None

    def start(m):
        r = by_id[m]
        zone = _admitted(r.get("zone"))
        if zone is None or not r.get("time") or not r.get("date"):
            return None
        d = date.fromisoformat(r["date"])
        return datetime(d.year, d.month, d.day, *_clock(r["time"]),
                        tzinfo=zone).timestamp()

    chain = {}                       # pair -> its first zoned member
    for m, pair in zip(legs, pairs):
        if pair not in chain and start(m) is not None:
            chain[pair] = m
    if len(chain) < 2 or not route_chain(list(chain.values()), by_id):
        return None, "pairs disagree"
    ordered = sorted(chain.values(), key=start)
    origin = route_pair(by_id[ordered[0]])[0]
    destination = route_pair(by_id[ordered[-1]])[1]
    if any(pair not in chain and pair != (origin, destination) for pair in pairs):
        return None, "pairs disagree"
    if destination == origin:
        return None, "round trip"
    return destination, None


def fact_sheet(grouping, run_date):
    """One row per spoken item, in calendar order, or None for a fallback
    grouping (ruled 2026-11-23). Code decides every fact; script() only
    words them.

    Calls select() once (its WAVETOPS SELECT line is the fallback's one log
    line) and no_flight_trip() once. select()'s ranking decides the cut; the
    trip line counts as one of CAP, so when the selection is already full
    its lowest-ranked group - the last - is dropped, and logged. Nothing
    else is ever dropped.

    Each row is a dict with the same keys:
      kind         "flight" | "meeting" | "trip"
      weekday      the day spoken (a trip: its first day)
      time         the spoken clock, or None
      zone         the zone word, or None (Eastern, bare, or unknown)
      destination  the spoken city, or None (flight and trip rows)
      title        the cleaned title (meeting rows), else None
      through      a trip's last weekday when it differs from the first and
                   falls inside the window, else None
      open_ended   True when a trip's last date falls past run_date + 6

    Order: the spoken day; on one day the trip row, then items with no
    clock, then clocked items by their spoken (hour, minute); file order
    last. Prints WAVETOPS DESTINATION DROPPED <reason>, WAVETOPS ZONE
    UNKNOWN <key> and WAVETOPS CAP DROPPED 1 as they occur - never a title,
    a code or an id (destinations.spoken_destination names an unknown code
    on its own DESTINATION UNKNOWN line).
    """
    chosen, _evidence = select(grouping, run_date)
    groups = grouping.get("groups")
    if groups is None:
        return None
    from destinations import spoken_destination
    by_id = {r["id"]: r for r in grouping.get("records") or []}
    position = {id(g): i for i, g in enumerate(groups)}
    trip = no_flight_trip(grouping, run_date, chosen)
    if trip is not None and len(chosen) == CAP:
        chosen = chosen[:-1]
        print("WAVETOPS CAP DROPPED 1")

    keyed = []
    for group in chosen:
        day, clock, word, order = _spoken_when(group, by_id)
        row = {"kind": "meeting", "weekday": _weekday(day),
               "time": None if clock is None else _spoken_clock(clock),
               "zone": word, "destination": None, "title": None,
               "through": None, "open_ended": False}
        if _is_travel(group, by_id):
            row["kind"] = "flight"
            code, reason = _final_destination(group, by_id)
            if code is not None:
                row["destination"] = spoken_destination(code)
                if row["destination"] is None:
                    reason = "unknown code"
            if reason is not None:
                print(f"WAVETOPS DESTINATION DROPPED {reason}")
        else:
            row["title"] = _spoken_title(by_id[group["primary"]].get("summary"))
        keyed.append(((day.isoformat(),) + order + (position[id(group)],), row))

    if trip is not None:
        destination, first, last = trip
        if destination is None:
            print("WAVETOPS DESTINATION DROPPED no single lodging city")
        first_day, last_day = date.fromisoformat(first), date.fromisoformat(last)
        open_ended = last_day > run_date + timedelta(days=WINDOW_DAYS - 1)
        through = None if open_ended or last_day == first_day else _weekday(last_day)
        keyed.append(((first, 0, 0, 0, 0),
                      {"kind": "trip", "weekday": _weekday(first_day),
                       "time": None, "zone": None, "destination": destination,
                       "title": None, "through": through,
                       "open_ended": open_ended}))

    keyed.sort(key=lambda pair: pair[0])
    return [row for _, row in keyed]


GREETING = "Good Evening Nick."
CLEAR_WEEK = "The week ahead is clear."


def _line(row):
    """One fact-sheet row as its sentence: one line, exactly one closing
    period (patterns ruled 2026-11-23).

      flight          <Day> at <time>[ <Zone>], flight to <destination>.
      flight, no dest <Day> at <time>[ <Zone>], flight.
      meeting         <Day> at <time>[ <Zone>], <title>.
      no clock        <Day>, flight to <destination>. / <Day>, flight. /
                      <Day>, <title>.
      trip            Traveling to <destination>, <Day> through <Day>.
        one day       Traveling to <destination> on <Day>.
        past window   Traveling to <destination> from <Day>.
        no dest       the same three without "to <destination>".
    """
    if row["kind"] == "trip":
        to = f" to {row['destination']}" if row["destination"] else ""
        if row["open_ended"]:
            return f"Traveling{to} from {row['weekday']}."
        if row["through"]:
            return f"Traveling{to}, {row['weekday']} through {row['through']}."
        return f"Traveling{to} on {row['weekday']}."
    when = row["weekday"]
    if row["time"]:
        when += f" at {row['time']}"
        if row["zone"]:
            when += f" {row['zone']}"
    if row["kind"] == "flight":
        what = f"flight to {row['destination']}" if row["destination"] else "flight"
    else:
        what = row["title"]
    return f"{when}, {what}."


def script(grouping, run_date):
    """The wave-tops script as a list of lines (ruled 2026-11-23): the
    greeting, then one to five sentences in calendar order - two to six
    lines. Nothing chosen and no trip: the greeting and the clear-week line.
    A fallback grouping: [] - no script; select()'s WAVETOPS SELECT line
    carries the cause. Writes no file."""
    rows = fact_sheet(grouping, run_date)
    if rows is None:
        return []
    return [GREETING] + ([_line(row) for row in rows] or [CLEAR_WEEK])


def handoff(script_text):
    """Hand the script to the video step (not built)."""
    raise NotImplementedError
