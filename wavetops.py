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
  travel if any member is; select() returns groups.
- Travel: a FLIGHT <n> identifier on any member (one is enough;
  _is_journey is the renderer's two-flight test and is not used here), a
  non-null group container, or synthesize.codes_only(primary) returning a
  pair. The Travel-calendar signal is not persisted - recorded gap; trigger:
  a leg on that calendar no other signal catches.
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

from synthesize import codes_only

CAP = 5
WINDOW_DAYS = 7          # run_date through run_date + 6
TASK_KINDS = ("todo", "attention")
MINUTES_PER_DAY = 24 * 60


def _is_travel(group, by_id):
    """Any member carries a FLIGHT identifier, the container is non-null, or
    codes_only(primary) returns a pair."""
    for m in group["members"]:
        if any(i.startswith("FLIGHT ") for i in by_id[m].get("identifiers") or []):
            return True
    if group.get("container") is not None:
        return True
    return codes_only(by_id[group["primary"]]) is not None


def _is_task(primary):
    """The primary's kind is a task kind."""
    return primary.get("kind") in TASK_KINDS


def _has_place(primary):
    """The primary's location is non-empty."""
    return primary.get("location", "") != ""


def _is_all_day(primary):
    return (primary.get("source") == "calendar" and primary.get("kind") == "event"
            and primary.get("time") is None)


def _clock(text):
    hours, minutes = text.split(":")
    return int(hours), int(minutes)


def _duration_minutes(primary, run_date):
    """Timed: end - start, end-before-start read as next day. All-day: the
    remaining span from run_date in days, as minutes. No end: 0."""
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
    chronological. Prints one WAVETOPS SELECT line. Returns group dicts."""
    groups = grouping.get("groups")
    if groups is None:
        _log(0, 0, f"fallback: {grouping.get('cause')}")
        return []
    by_id = {r["id"]: r for r in grouping.get("records") or []}
    last = run_date + timedelta(days=WINDOW_DAYS - 1)

    travel, other = [], []
    for index, group in enumerate(groups):
        primary = by_id[group["primary"]]
        if primary.get("date") is None:
            continue
        if not run_date <= date.fromisoformat(primary["date"]) <= last:
            continue
        if _is_task(primary):
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
    return chosen


def script(selection):
    """Write the wave-tops script for a selection (not built)."""
    raise NotImplementedError


def handoff(script_text):
    """Hand the script to the video step (not built)."""
    raise NotImplementedError
