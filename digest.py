from datetime import date, datetime, time, timedelta
from googleapiclient.discovery import build
from fetch_gmail import get_credentials, fetch_recent_messages
from fetch_calendar import fetch_upcoming_events, DEFAULT_TZ as HOME_TZ
from extract import extract_commitments
from reminders_write import reconcile, write_back
from synthesize import build_records, filter_time_conflict, synthesize
from zoneinfo import ZoneInfo
from state import load_state, save_state, STATE_FILE

QUERY = "newer_than:2d"
MAX_RESULTS = 200
HORIZON_DAYS = 7

def process_messages(messages, state, today):
    """Fold newly fetched messages into state["commitments"].

    Returns (skipped, extracted, failed) counts:
      - skipped: messages already processed or with no readable body
      - extracted: messages successfully run through the extraction brain
      - failed: list of subjects whose extraction raised
    """
    processed = state["processed_message_ids"]
    commitments = state["commitments"]
    skipped = 0
    extracted = 0
    failed = []
    for msg in messages:
        # Already handled on a previous run — never re-extract.
        if msg["id"] in processed:
            skipped += 1
            continue
        if msg["body"] is None:
            skipped += 1
            continue
        try:
            results = extract_commitments(msg["body"])
        except Exception:
            # Leave the id out of processed_message_ids so a future run retries.
            failed.append(msg["subject"] or "(no subject)")
            continue
        # Extraction succeeded: remember the message and fold in every commitment.
        processed.append(msg["id"])
        extracted += 1
        for position, commitment in enumerate(results):
            key = f"{msg['id']}:{position}"
            # Never overwrite an existing record — its status/dates are canonical.
            if key in commitments:
                continue
            record = {
                "id": key,
                "status": "open",
                "first_seen": today,
                "resolved_on": None,
            }
            # Fold in all extraction fields, plus source message context.
            record.update(commitment)
            record["subject"] = msg["subject"]
            record["sender"] = msg["sender"]
            commitments[key] = record
    return skipped, extracted, failed

def parse_iso_date(value):
    """Parse a model-produced date string. Anything unparseable is treated
    as no date — never crash the digest on bad model output."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None

def weekday_date(d):
    """The one weekday-date format in this file, e.g. 'Thu, Aug 6'.
    Accepts a date or a datetime."""
    return f"{d.strftime('%a, %b')} {d.day}"

def format_date(value):
    """Humanize an ISO date string, e.g. '2026-08-06' -> 'Thu, Aug 6'.
    Routes through parse_iso_date; returns the raw value unchanged if unparseable."""
    if not value:
        return "no date"
    d = parse_iso_date(value)
    if d is None:
        return value
    return weekday_date(d)

def format_time(value):
    """Humanize a 24-hour time string, e.g. '13:00' -> '1:00 PM'.
    Returns None for a falsy value, and the raw value unchanged if it does not
    parse — never raises, so bad model output can't crash the digest."""
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except (TypeError, ValueError):
        return value
    return parsed.strftime("%I:%M %p").lstrip("0")

def partition_commitments(commitments, today):
    """Pure read over open commitments. today is a date object.
    Returns (attention, todo, appointments).

    Dateless items are always to-do — there is nothing to be late for yet.
    Appointments belong on the calendar timeline: past ones are dropped (the
    day is gone), and ones beyond HORIZON_DAYS stay hidden until they come
    into range. Everything else is overdue-to-attention or due-soon-to-todo.
    Never mutates."""
    horizon = today + timedelta(days=HORIZON_DAYS)
    attention = []
    todo = []
    appointments = []
    for c in commitments.values():
        if c["status"] != "open":
            continue
        d = parse_iso_date(c.get("date"))
        if d is None:
            todo.append(c)
        elif c.get("type") == "appointment":
            if c.get("calendar", {}).get("status") == "approved":
                continue        # Google Calendar holds it; the fetch will surface it
            if d < today:
                continue
            if d <= horizon:
                appointments.append(c)
        else:
            if d < today:
                attention.append(c)
            elif d <= horizon:
                todo.append(c)
    return attention, todo, appointments

def _index_grouping(grouping):
    """Join a synthesis grouping back to renderable items.

    Returns (ref_to_group, by_rid), or (None, None) when there is no grouping
    to apply — grouping absent, or its groups is None (fallback: network,
    validation, or synthesis off). An EMPTY groups list is a real grouping
    over zero records and is not the fallback."""
    if not grouping or grouping.get("groups") is None:
        return None, None
    by_rid = {r["id"]: r for r in grouping["records"]}
    ref_to_group = {}
    for g in grouping["groups"]:
        for m in g["members"]:
            ref_to_group[tuple(by_rid[m]["ref"])] = g
    return ref_to_group, by_rid


def _fold(items, ref_of, ref_to_group, by_rid):
    """Return [(item, group_or_None)] with non-primary members dropped.

    Each group renders once, from its primary. Groups never cross sections
    (validate() holds every group to one date and one kind-family), so the
    primary is always among items; if it ever is not, the first member seen
    stands in rather than the whole group vanishing."""
    if ref_to_group is None:
        return [(it, None) for it in items]
    present = {ref_of(it) for it in items}
    shown = set()
    out = []
    for it in items:
        ref = ref_of(it)
        g = ref_to_group.get(ref)
        if g is None:
            out.append((it, None))
            continue
        if id(g) in shown:
            continue
        primary_ref = tuple(by_rid[g["primary"]]["ref"])
        if ref != primary_ref and primary_ref in present:
            continue
        shown.add(id(g))
        out.append((it, g))
    return out


def _conflict_text(group, by_rid):
    """Render a group's conflicts from the MEMBERS' real values — the model
    named the field, it never picked a winner. Times carry a zone abbreviation
    when the record's zone is known and not HOME_TZ.

    Conflicts are code-derived (ruled 2026-09-04): synthesize.derive_conflicts
    computes them from the records after validation, and time is the only
    field it emits — the model is no longer asked, because conflicts were the
    one axis that moved between otherwise identical live runs. Rendering is
    unchanged; the location/date/summary branches below are reachable only for
    a grouping built elsewhere.

    Guard (ruled 2026-09-04): an all-day record has no time to disagree with.
    filter_time_conflict narrows a time conflict to the members that carry a
    time and drops it when fewer than two distinct times remain. The derivation
    satisfies that by construction, so this call is defending a grouping that
    did not come through validate() — a fixture recorded before the ruling."""
    parts = []
    for raw in group["conflicts"]:
        c = filter_time_conflict(raw, by_rid)
        if c is None:
            continue
        values = []
        for m in c["members"]:
            r = by_rid[m]
            if c["field"] == "time":
                v = format_time(r.get("time")) or "all day"
                zone = r.get("zone")
                if zone and zone != HOME_TZ and r.get("time") and r.get("date"):
                    try:
                        inst = datetime.combine(
                            date.fromisoformat(r["date"]),
                            datetime.strptime(r["time"], "%H:%M").time(),
                            tzinfo=ZoneInfo(zone))
                        v += " " + inst.strftime("%Z")
                    except (ValueError, KeyError):
                        pass
            elif c["field"] == "location":
                v = (r.get("location") or "none")[:40]
            elif c["field"] == "date":
                v = format_date(r.get("date"))
            else:
                v = None
            if v is not None and v not in values:
                values.append(v)
        if values:
            parts.append(f"{c['field']}: " + " / ".join(values))
        else:
            parts.append(c["field"])
    return "(sources disagree on " + "; ".join(parts) + ")" if parts else ""


def render_attention(items, grouping=None):
    """Build the overdue block as a list of lines, sorted by date ascending.
    Every item here has a parseable date by construction. No day counts — the
    date says it. Grouped members fold into their primary. Returns [] if there
    is nothing overdue."""
    if not items:
        return []
    ref_to_group, by_rid = _index_grouping(grouping)
    ordered = sorted(items, key=lambda c: parse_iso_date(c["date"]))
    lines = ["NEEDS ATTENTION"]
    for c, _ in _fold(ordered, lambda c: ("commitment", c["id"]), ref_to_group, by_rid):
        lines.append(f"• {c['what']} — was due {format_date(c['date'])}")
    return lines

def render_todo(items, grouping=None):
    """Build the to-do block as a list of lines: dated items first, sorted by
    date ascending, then dateless items in their existing order. Grouped
    members fold into their primary. Returns [] if there is nothing to do."""
    if not items:
        return []
    ref_to_group, by_rid = _index_grouping(grouping)
    dated = [c for c in items if parse_iso_date(c.get("date")) is not None]
    dateless = [c for c in items if parse_iso_date(c.get("date")) is None]
    dated.sort(key=lambda c: parse_iso_date(c["date"]))
    lines = ["TO DO"]
    for c, _ in _fold(dated + dateless, lambda c: ("commitment", c["id"]), ref_to_group, by_rid):
        if parse_iso_date(c.get("date")) is not None:
            lines.append(f"• {c['what']} — due {format_date(c['date'])}")
        else:
            lines.append(f"• {c['what']}")
    return lines

def _zone_suffix(dt, labeled):
    """The zone abbreviation, as " EDT", for an aware datetime that should
    carry it — otherwise the empty string.

    %Z on an aware datetime resolves EDT/EST correctly for the instant and
    costs far fewer bytes than the IANA key. A naive datetime has no key and
    is never labeled: "None" in a digest line is worse than no label at all.
    """
    abbr = dt.strftime("%Z")
    key = getattr(dt.tzinfo, "key", None)
    return f" {abbr}" if labeled and key and abbr else ""


def _time_label(start, end=None, labeled=False):
    """The time part of a timeline line, built from aware datetimes.

    Extracted from merge_coming_up 2026-09-16 with its behaviour unchanged,
    so one function owns the format wherever a line needs it.

    labeled applies to the SINGLE-ENDPOINT form only. It forces the zone
    abbreviation on regardless of HOME_TZ, and defaults False so every caller
    that predates it renders as before. The journey renderer sets it when a
    journey's departure and arrival zones differ: those are two separate
    lines, so neither call can see the other's zone, and the cross-zone rule
    below has nothing to compare. The range form ignores it — nothing calls a
    range with it set, and unproven behaviour does not belong in the renderer
    (ruled 2026-09-16).

    A bare time implicitly claims the machine's zone, so any endpoint that
    isn't in HOME_TZ is labeled, and BOTH endpoints are labeled when the two
    differ - one suffix would otherwise appear to cover both. The comparison
    is on the IANA key, which is stable (HOME_TZ never equals "EDT"); the
    display is the abbreviation.

    With no end, the label is the single start endpoint, labeled by the same
    not-HOME_TZ rule. With an end, it is the range, and a shared meridiem
    collapses to one ("10:00-11:00 AM") unless the zones differ, where
    collapsing would hide which clock belongs to which zone.
    """
    start_zone = getattr(start.tzinfo, "key", None)
    s_clock = start.strftime("%I:%M").lstrip("0")
    s_ampm = start.strftime("%p")
    if end is None:
        return f"{s_clock} {s_ampm}" + _zone_suffix(
            start, labeled or (bool(start_zone) and start_zone != HOME_TZ))

    end_zone = getattr(end.tzinfo, "key", None)
    cross_zone = bool(start_zone and end_zone and start_zone != end_zone)
    e_clock = end.strftime("%I:%M").lstrip("0")
    e_ampm = end.strftime("%p")
    e_suffix = _zone_suffix(
        end, bool(end_zone) and (cross_zone or end_zone != HOME_TZ))
    if s_ampm == e_ampm and not cross_zone:
        return f"{s_clock}–{e_clock} {e_ampm}{e_suffix}"
    s_suffix = _zone_suffix(
        start, bool(start_zone) and (cross_zone or start_zone != HOME_TZ))
    return f"{s_clock} {s_ampm}{s_suffix}–{e_clock} {e_ampm}{e_suffix}"


def merge_coming_up(events, appointment_commitments, today):
    """Normalize calendar events and appointment commitments into one
    date-sorted timeline. Each item is {sort_key, ref, date, time_label, summary}.

    ref mirrors synthesize.build_records exactly — ("event", id, "start" |
    "end" | "single") or ("commitment", key) — so a grouping computed over
    those records joins back to these items. time_label is the time part
    only; the day renders once per day-block (S5), never per line.

    S2 (ruled 2026-09-03): a multi-day all-day span renders as endpoints
    only — a start-day item ("all day") and a last-day item ("Last day"),
    nothing in between. A start day already past emits no item; a last day
    beyond the horizon emits none either. This replaces the "Now–<end>"
    in-progress line.

    sort_key's first element is always a date, never a datetime — comparing the
    two raises TypeError, and all-day events already carry plain dates."""
    horizon = today + timedelta(days=HORIZON_DAYS)
    merged = []

    def add(d, start_time, ref, time_label, summary):
        merged.append({
            "sort_key": (d, start_time),
            "ref": ref,
            "date": d,
            "time_label": time_label,
            "summary": summary,
        })

    for e in events:
        if e["all_day"]:
            start = e["start_local"]
            # Google all-day end dates are EXCLUSIVE: an Aug 2-8 stay
            # arrives with end 2026-08-09. Subtract one day for the
            # true last day.
            last = e["end_local"] - timedelta(days=1)
            if last <= start:
                add(start, time.min, ("event", e["id"], "single"), "all day", e["summary"])
                continue
            if start >= today:
                add(start, time.min, ("event", e["id"], "start"), "all day", e["summary"])
            if last <= horizon:
                add(last, time.min, ("event", e["id"], "end"), "Last day", e["summary"])
        else:
            d = e["start_local"].date()
            start_time = e["start_local"].time()
            add(d, start_time, ("event", e["id"], "single"),
                _time_label(e["start_local"], e["end_local"]), e["summary"])
    for c in appointment_commitments:
        d = parse_iso_date(c["date"])
        pretty = format_time(c.get("time"))
        try:
            start_time = datetime.strptime(c.get("time"), "%H:%M").time()
        except (TypeError, ValueError):
            # Unparseable or absent: sort it to the head of its day.
            start_time = time.min
        add(d, start_time, ("commitment", c["id"]), pretty, c["what"])
    merged.sort(key=lambda m: m["sort_key"])
    return merged


ANCHOR_RUN = 3


def _anchor_tokens(summary):
    """Case-fold, drop punctuation, split on whitespace. "Check-in at
    North/Everett" and "check in at north everett" tokenize identically."""
    return "".join(ch if ch.isalnum() else " " for ch in summary.lower()).split()


def _shares_anchor(a, b):
    """True when token lists a and b are identical, or share a contiguous
    run of ANCHOR_RUN tokens in the same order. Set membership on n-grams:
    any longer shared run contains a run of exactly ANCHOR_RUN."""
    if a == b:
        return True
    if len(a) < ANCHOR_RUN or len(b) < ANCHOR_RUN:
        return False
    grams = {tuple(a[i:i + ANCHOR_RUN]) for i in range(len(a) - ANCHOR_RUN + 1)}
    return any(tuple(b[i:i + ANCHOR_RUN]) in grams
               for i in range(len(b) - ANCHOR_RUN + 1))


def collapse_display(merged):
    """Collapse display-duplicate timeline items. Display layer only: state
    keeps every record (one-stop-shop ruling), and nothing here resolves,
    deletes, or merges anything upstream of the renderer.

    Two items collapse when (ruled 2026-09-02, against the 9/2 live cluster):
      1. sort_key is equal — same date AND same start time. This is the term
         that carries every negative fixture: check-in vs check-out, two
         flight legs, an all-day stay beside its timed check-in, a recurring
         obligation on different days.
      2. Their summaries share an anchor: a contiguous run of ANCHOR_RUN
         normalized words. Byte comparison on normalized tokens — no
         similarity score, no threshold tuning. This is the exact-containment
         discriminator of 8/1 widened to the shape the live data actually
         takes: "Stay: X" beside "Stay at X" contains neither in the other.

    The survivor is the WHOLE item with the longest summary, label included —
    never a composite. Gluing one source's time range onto another's summary
    can state a false fact (a parser's leg-arrival time on a full-itinerary
    line).

    The survivor carried a "(xN)" marker until it was retired on both render
    paths (ruled 2026-09-14, removed 2026-09-15): the page reports, and
    membership correctness belongs to the synthesis contract, verified by
    replay_mornings.py and synth_dump.py rather than by a counter on the
    consumer surface. On this path the "(ungrouped - synthesis unavailable)"
    note render_coming_up already prints is the trace that a collapse
    happened here at all.

    Grouping is transitive by any member (A~B and B~C group all three)."""
    groups = []
    for item in merged:
        toks = _anchor_tokens(item["summary"])
        for g in groups:
            if g["key"] == item["sort_key"] and any(_shares_anchor(toks, t) for t in g["tokens"]):
                g["items"].append(item)
                g["tokens"].append(toks)
                break
        else:
            groups.append({"key": item["sort_key"], "tokens": [toks], "items": [item]})
    out = []
    for g in groups:
        # max() returns the first maximal element, so ties keep merge order.
        out.append(max(g["items"], key=lambda m: len(m["summary"])))
    return out


def _visible_timeline(merged, grouping):
    """The timeline as it will render: [(item, group_or_None)]. With a
    grouping, members fold into primaries; without one (fallback), the 9/2
    exact-anchor collapse_display path stands in."""
    ref_to_group, by_rid = _index_grouping(grouping)
    if ref_to_group is None:
        return [(it, None) for it in collapse_display(merged)], None
    return _fold(merged, lambda it: it["ref"], ref_to_group, by_rid), by_rid


def _is_journey(group, by_rid):
    """True when the group's members carry two or more distinct "FLIGHT "
    identifiers (ruled 2026-09-16).

    Exact string comparison over the identifiers build_records already
    extracted — no parsing here, no fuzzy match. Two flight numbers on one
    date is the shape prompt rule 3 merges into one journey; a single flight
    number is an ordinary flight and keeps its range line unchanged.
    """
    flights = {identifier
               for m in group["members"]
               for identifier in (by_rid[m].get("identifiers") or [])
               if identifier.startswith("FLIGHT ")}
    return len(flights) >= 2


def _record_instant(record, clock_key, zone_key):
    """A record's clock as an instant, or None when the pair is absent or
    unparseable. Rebuilt from date + clock + ZoneInfo(zone), the way
    _conflict_text rebuilds one to resolve an abbreviation. Never raises: a
    display decision, not validation."""
    clock = record.get(clock_key)
    zone = record.get(zone_key)
    day = record.get("date")
    if not clock or not zone or not day:
        return None
    try:
        return datetime.combine(date.fromisoformat(day),
                                datetime.strptime(clock, "%H:%M").time(),
                                tzinfo=ZoneInfo(zone))
    except (ValueError, KeyError):
        return None


def _end_instant(record):
    """The record's end as an instant, or None when it carries no end pair.

    A record holds clocks, not instants, so an end that lands before its own
    start is the next day — a red-eye landing at 6:15 AM after a 9:35 PM
    departure — and rolls forward one day.
    """
    end = _record_instant(record, "end_time", "end_zone")
    if end is None:
        return None
    start = _record_instant(record, "time", "zone")
    if start is not None and end < start:
        end += timedelta(days=1)
    return end


def _framed(instant, day):
    """The instant's clock and zone, moved onto an item-frame day.

    The displayed instant is rebuilt on the date actually shown, so %Z
    resolves the abbreviation for that day rather than for whatever day the
    record was recorded on (ruled 2026-09-16).
    """
    return datetime.combine(day, instant.time(), tzinfo=instant.tzinfo)


def _journey_items(item, group, by_rid):
    """A journey group's display items: Departure, and Arrival when some
    member carries an end (ruled 2026-09-16).

    THE FRAME RULE (ruled 2026-09-16): in the renderer a record may supply a
    clock, a zone, and a day DIFFERENCE — never an absolute date. Absolute
    dates come from merged items only. A recorded grouping replays against
    items rebuilt today, so a record's date sits in the frame it was recorded
    in while every item sits in today's; reading the date off a record put
    this Arrival line three days behind the rest of its own trip, under a day
    header of its own. The offset — 0, or 1 for a red-eye — is a difference,
    so it travels; the day it lands on is the primary item's.

    One journey is one obligation, so its legs never earn their own lines —
    but the two endpoints are what change what Nick does, and a range built
    from one leg stated the wrong arrival. Departure renders the primary's
    start; Arrival renders the member whose end instant is LATEST, which is
    the journey's true arrival however the legs are ordered. Ties go to the
    primary when it is among them, else the first in member order.

    The Arrival is its own timeline item, dated and sorted by the arrival
    (ruled 2026-09-16), keyed the way every other timed item is keyed: the
    local date and clock of the endpoint, in its own zone.

    Both endpoints carry their zone when the two differ, which is the
    existing cross-zone rule applied across two lines instead of within one.

    DISPLAY ONLY: these items are built here, rendered here, and reach
    nothing upstream — not state, not identity, not Reminders. Nothing
    returns them; render_coming_up turns them into strings.
    """
    primary = by_rid[group["primary"]]
    start = _record_instant(primary, "time", "zone")
    ends = [(by_rid[m], _end_instant(by_rid[m])) for m in group["members"]]
    ends = [(record, instant) for record, instant in ends if instant is not None]

    departure = {**item, "summary": f"Departure: {item['summary']}",
                 "journey": True}
    if not ends:
        # No member carries an end: the Departure line stands alone.
        if start is not None:
            departure["time_label"] = _time_label(_framed(start, item["date"]))
        return [(departure, group)]

    latest = max(instant for _, instant in ends)
    tied = [record for record, instant in ends if instant == latest]
    arrival = next((record for record in tied if record["id"] == primary["id"]),
                   tied[0])
    cross = bool(primary.get("zone") and arrival.get("end_zone")
                 and primary["zone"] != arrival["end_zone"])
    if start is not None:
        departure["time_label"] = _time_label(
            _framed(start, item["date"]), labeled=cross)

    # The day difference travels; the absolute date does not. Both sides of
    # the subtraction are in the record frame, and the roll is already in
    # latest, so the result is 0 or 1 — computed, never assumed.
    primary_day = None
    if primary.get("date"):
        try:
            primary_day = date.fromisoformat(primary["date"])
        except ValueError:
            primary_day = None
    offset_days = (latest.date() - primary_day).days if primary_day else 0
    arrival_date = item["date"] + timedelta(days=offset_days)
    shown = _framed(latest, arrival_date)
    arrival_item = {
        "sort_key": (arrival_date, shown.time()),
        "ref": tuple(arrival["ref"]),
        "date": arrival_date,
        "time_label": _time_label(shown, labeled=cross),
        "summary": f"Arrival: {arrival['summary']}",
        "journey": True,
    }
    return [(departure, group), (arrival_item, group)]


def render_coming_up(merged, grouping=None):
    """Build the timeline in the day-block register (S5, ruled 2026-09-03):
    one unbulleted header per day — "DDD, Mon D", plus " — <name>: <phase>"
    when a group on that day carries a trip container — then indented lines
    "  • <time> — <summary>". The renderer prints a container title if a group
    supplies one and never inspects it.

    Fallback (ruled 2026-09-04, option A): same register, bare headers, the
    collapse_display path, and one note line naming why. Purely
    informational — never touches state. Returns [] if the timeline is empty."""
    if not merged:
        return []
    visible, by_rid = _visible_timeline(merged, grouping)
    if by_rid is not None:
        # Journey groups render as two endpoint lines; the sort restores
        # timeline order once the Arrival joins. Stable, so every other item
        # keeps its place. The fallback and synthesis-off paths have no
        # groups and never reach here.
        expanded = []
        for it, g in visible:
            if g and _is_journey(g, by_rid):
                expanded.extend(_journey_items(it, g, by_rid))
            else:
                expanded.append((it, g))
        expanded.sort(key=lambda pair: pair[0]["sort_key"])
        visible = expanded
    lines = ["COMING UP"]
    if by_rid is None:
        cause = (grouping or {}).get("cause")
        lines.append("(ungrouped — synthesis off)" if cause == "off"
                     else "(ungrouped — synthesis unavailable)")
    containers = {}
    for it, g in visible:
        if g and g["container"] and it["date"] not in containers:
            containers[it["date"]] = g["container"]
    current = None
    for it, g in visible:
        if it["date"] != current:
            current = it["date"]
            header = weekday_date(current)
            c = containers.get(current)
            if c:
                header += f" — {c['name']}: {c['phase']}"
            lines.append(header)
        text = it["summary"]
        # A journey's legs disagreeing about departure time is the grouping
        # working, not two sources contradicting each other: its endpoints
        # now render as their own lines (ruled 2026-09-16).
        if g and g["conflicts"] and not it.get("journey"):
            conflict = _conflict_text(g, by_rid)
            if conflict:
                text += " " + conflict
        if it["time_label"]:
            lines.append(f"  • {it['time_label']} — {text}")
        else:
            lines.append(f"  • {text}")
    return lines

def build_digest(state, events, today, compact_calendar=False, partition=None,
                 grouping=None):
    """Compose the digest as a (title, body) tuple, excluding the run summary.

    title is the header line; body is everything after it with no leading blank
    line. Reassembling as title + "\\n\\n" + body reproduces the single-string
    digest exactly. When compact_calendar is True, the COMING UP section
    collapses to one summary line (an empty calendar still renders nothing).
    The flag changes rendering only — classification and section order are
    identical regardless of it.

    partition, when given, is a precomputed (attention, todo, appointments)
    triple; run_digest passes the same one to the render so classification
    happens once per run and write_back acts on exactly what gets rendered.
    When None it is computed here, which is what preview_digest.py and any
    other direct caller rely on. Pure either way — this function reads state
    and renders strings, and never reaches Reminders.

    grouping, when given, is {"records", "groups", "cause"} as run_digest
    builds it from synthesize(); groups None means the fallback path, with
    cause naming why. When None, the fallback renders with cause "off" — this
    function never calls the API, so preview_digest.py stays $0 unless it
    passes a grouping it recorded live."""
    d = parse_iso_date(today)
    title = f"Digest — {weekday_date(d)}"
    if partition is None:
        partition = partition_commitments(state["commitments"], d)
    if grouping is None:
        grouping = {"records": None, "groups": None, "cause": "off"}
    attention, todo, appointments = partition
    merged = merge_coming_up(events, appointments, d)
    attention_lines = render_attention(attention, grouping)
    todo_lines = render_todo(todo, grouping)
    if compact_calendar:
        # Count the merged timeline, not events — appointments live there too,
        # and folded members are not separate lines.
        n = len(_visible_timeline(merged, grouping)[0])
        noun = "event" if n == 1 else "events"
        cal_lines = [f"COMING UP: {n} {noun} in the next {HORIZON_DAYS} days"] if n else []
    else:
        cal_lines = render_coming_up(merged, grouping)

    if not attention_lines and not todo_lines and not cal_lines:
        return title, "Nothing needs your attention today."

    body_lines = []
    for section in (attention_lines, todo_lines, cal_lines):
        if section:
            if body_lines:
                body_lines.append("")
            body_lines += section
    return title, "\n".join(body_lines)

def fetch_inputs():
    """Phase 1 — the only network in the run. Returns (messages, events)."""
    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds)
    messages = fetch_recent_messages(service, QUERY, MAX_RESULTS)
    events = fetch_upcoming_events(HORIZON_DAYS)
    return messages, events

def run_digest():
    """Run the full digest: fetch, extract, reconcile, partition, write back,
    then render. Prints the digest and run summary. Returns (title, body) —
    the full render, and the only one. There is no compact render: an oversized
    body is split across messages by deliver.py rather than having a section
    collapsed out of it.

    The phase order matters. reconcile runs before the partition so ticked
    reminders drop out of today's digest rather than being re-listed and
    re-created. The partition runs exactly once: write_back creates reminders
    for the same NEEDS ATTENTION and TO DO items the render shows, so the
    Reminders list and the sent digest can never disagree."""
    today = date.today().isoformat()
    today_d = parse_iso_date(today)

    state = load_state(STATE_FILE)

    messages, events = fetch_inputs()

    skipped, extracted, failed = process_messages(messages, state, today)
    save_state(state, STATE_FILE)

    reconcile(state)

    partition = partition_commitments(state["commitments"], today_d)
    attention, todo, appointments = partition
    # Synthesis runs once per run over exactly the partition that renders,
    # so the grouping the page shows is the grouping write_back sees (S4).
    records = build_records(events, attention, todo, appointments, today_d)
    groups, cause = synthesize(records, today_d)
    grouping = {
        "records": records,
        "groups": groups["groups"] if groups is not None else None,
        "cause": cause,
    }
    write_back(state, attention, todo, grouping=grouping)

    title, full_body = build_digest(state, events, today, partition=partition,
                                    grouping=grouping)
    print(title + "\n\n" + full_body)

    open_total = sum(1 for c in state["commitments"].values() if c["status"] == "open")
    print(f"\n--- RUN SUMMARY ---")
    print(f"Messages fetched: {len(messages)}")
    print(f"Skipped (already processed / unreadable): {skipped}")
    print(f"Newly extracted: {extracted}")
    print(f"Extraction failures: {len(failed)}")
    for subj in failed:
        print(f"  - {subj}")
    print(f"Total open in state: {open_total}")

    return title, full_body

if __name__ == "__main__":
    run_digest()
