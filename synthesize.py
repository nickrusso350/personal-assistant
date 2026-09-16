"""Synthesis stage: one Claude API call per run over the day's assembled
records, returning a GROUPING that code validates and the renderer uses.

Design record: Synthesis_Design_2026-09-03.md. Rulings S1-S6 (2026-09-03).

The contract: the model proposes structure only, never facts. It returns
which record ids are one real-world obligation, which member is primary,
an optional trip container {name, phase}, and which fields the members
disagree on. The renderer builds every line from the primary record's real
fields. The model never emits a time, a code, or a flight number, so a
hallucinated fact is impossible by construction.

Extraction (extract.py, per message) is untouched. State identity
(gmail message id, array position) is untouched. This stage is display and
reminder-minting only, and it never touches Reminders or state itself.

S6: synthesis never costs the morning. Network failures get a bounded retry
(pure read; the writes-never-retry ruling is not reopened). On exhaustion or
validation failure the caller renders the fallback path (collapse_display)
and the cause is logged as "network" or "validation" - they mean different
things.
"""

import json
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import sys
import time as _time
from datetime import date, timedelta

import anthropic

from env_loader import load_env_file

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2000
RETRIES = 3
BACKOFF_SECONDS = (1, 3)

DO_KINDS = ("todo", "attention")
HAPPEN_KINDS = ("event", "appointment")

# Identifier hints handed to the model alongside the text. Codes: 5-10 chars
# of upper-case letters and digits with at least one of each (confirmation
# codes, record locators). Numbers: 6+ digits (reservation numbers). Flights:
# a carrier word followed by 2-4 digits, normalized to "FLIGHT <n>".
_CODE_RE = re.compile(r"\b(?=[A-Z0-9]*[0-9])(?=[A-Z0-9]*[A-Z])[A-Z0-9]{5,10}\b")
_NUMBER_RE = re.compile(r"\b\d{6,}\b")
_FLIGHT_RE = re.compile(
    r"\b(?:flight|delta|united|american|southwest|alaska|jetblue|dl|ua|aa|wn|as|b6)"
    r"\s*#?\s*(\d{2,4})\b",
    re.IGNORECASE,
)


def _log(kind, payload):
    """One line per entry, JSON payload, SYNTHESIS prefix. stdout is where
    launchd sends the run, so this lands in digest.out beside RUN START."""
    print(f"SYNTHESIS {kind} {json.dumps(payload, default=str)}")


def extract_identifiers(*texts):
    """Identifier hints from free text. Deduplicated, order preserved."""
    found = []
    for text in texts:
        if not text:
            continue
        for match in _CODE_RE.findall(text):
            found.append(match)
        for match in _NUMBER_RE.findall(text):
            found.append(match)
        for match in _FLIGHT_RE.findall(text):
            found.append(f"FLIGHT {match}")
    seen = set()
    out = []
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _snippet(text, limit=200):
    if not text:
        return ""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + "..."


def build_records(events, attention, todo, appointments, today):
    """Id-stamped, normalized records for the model. today is a date.

    Returns a list of dicts; each carries a "ref" the renderer and write_back
    use to find the underlying item again:
      ("event", event_id, "start"|"end"|"single") for calendar events
      ("commitment", key) for commitments
    An all-day span yields TWO records - start day and last day - so S2
    (endpoints only) and S1 (same date, always) compose. A start day already
    past yields no start record: it would never render.

    A field written here reaches the model only if PROMPT_FIELDS names it
    (ruled 2026-09-14): the allowlist is withhold-by-default, so a field added
    for the renderer's or write_back's benefit stays out of the model's view
    until it is listed there deliberately.

    Timed events carry the end as its own pair, end_time and end_zone:
    fetch_calendar._parse_endpoint resolves each endpoint's timeZone
    separately, so a cross-zone leg ends in the arrival zone, and an end clock
    without its zone would be a clock string rather than an instant (the
    distinction ruled 2026-09-14 for conflicts). All-day events and
    commitments carry None for both, exactly as time and zone do.
    """
    records = []

    def add(kind, source, ref, when, when_time, zone, end_date, summary,
            location, description, end_time=None, end_zone=None):
        rid = f"r{len(records) + 1}"
        records.append({
            "id": rid,
            "ref": ref,
            "kind": kind,
            "source": source,
            "date": when.isoformat() if when else None,
            "time": when_time,
            "zone": zone,
            "end_time": end_time,
            "end_zone": end_zone,
            "end_date": end_date.isoformat() if end_date else None,
            "summary": summary or "",
            "location": _snippet(location, 120),
            "description_snippet": _snippet(description),
            "identifiers": extract_identifiers(summary, location, description),
        })

    for e in events:
        loc = e.get("location", "")
        desc = e.get("description", "")
        if e["all_day"]:
            start = e["start_local"]
            last = e["end_local"] - timedelta(days=1)   # exclusive end
            if last <= start:
                add("event", "calendar", ("event", e["id"], "single"), start,
                    None, None, None, e["summary"], loc, desc)
                continue
            if start >= today:
                add("event", "calendar", ("event", e["id"], "start"), start,
                    None, None, last, e["summary"], loc, desc)
            add("event", "calendar", ("event", e["id"], "end"), last,
                None, None, last, e["summary"], loc, desc)
        else:
            s = e["start_local"]
            end = e["end_local"]
            zone = getattr(s.tzinfo, "key", None)
            add("event", "calendar", ("event", e["id"], "single"), s.date(),
                s.strftime("%H:%M"), zone, None, e["summary"], loc, desc,
                end_time=end.strftime("%H:%M"),
                end_zone=getattr(end.tzinfo, "key", None))

    def add_commitment(kind, c):
        d = None
        if c.get("date"):
            try:
                d = date.fromisoformat(c["date"])
            except ValueError:
                d = None
        add(kind, "gmail", ("commitment", c["id"]), d, c.get("time"), None,
            None, c.get("what"), "", c.get("subject"))

    for c in attention:
        add_commitment("attention", c)
    for c in todo:
        add_commitment("todo", c)
    for c in appointments:
        add_commitment("appointment", c)
    return records


# Every field the model may see, in the order build_records writes them.
# An ALLOWLIST, not an exclusion (ruled 2026-09-14, "identity is code's
# domain"): a new key on a record is withheld until it is named here, so
# adding a field for the renderer's benefit cannot widen the model's view by
# accident. ref - and any other pipeline key, anything the pipeline mints or
# stores to find a record again - stays out by construction, because only
# these names can pass.
PROMPT_FIELDS = ("id", "kind", "source", "date", "time", "zone", "end_date",
                 "summary", "location", "description_snippet", "identifiers")


def _prompt(records, today):
    # "if k in r" reproduces the old k != "ref" filter exactly for a record
    # missing a field: absent stays absent, never a null the model must read.
    public = [{k: r[k] for k in PROMPT_FIELDS if k in r} for r in records]
    return """You are a precise grouping tool for a personal daily digest. You receive the day's records - calendar events and commitments extracted from email - and decide which records describe the SAME real-world obligation. You return structure only. You never restate, correct, or invent any fact.

Today's date is {today}.

Return ONLY a JSON object of exactly this shape. No preamble, no explanation, no markdown code fences. Write nothing before the opening brace.
{{"groups": [{{"members": ["r1", "r4"], "primary": "r4", "container": {{"name": "Seattle trip", "phase": "depart"}}}}]}}

Rules, in priority order:

What this is for: the digest gives Nick what he needs to know for the day and
the next seven days. It does not map out every moment. A record earns its own
line only if it changes what he would do; steps inside one obligation do not.

1. Every input id appears in exactly one group. A record that matches nothing is a group of one.
2. Two records are the same obligation only if they have the SAME date and either (a) they share a confirmation code, reservation number, or flight number, or (b) they plainly name the same vendor and the same thing (the same hotel check-in, the same car pickup). Different date means different obligation, always.
3. A journey booked as one trip is ONE obligation, even when it is flown as several legs on one day. Records sharing a confirmation code on one date are one group - itinerary records, per-leg records, and receipts alike - regardless of differing flight numbers, differing airports, or differing times. Connections are steps inside one journey, not separate obligations. Legs on different dates are different obligations, by rule 2.
4. Different things on one day stay separate: a flight, a car pickup, and a hotel check-in on one day are three groups. Two bookings from different vendors are two groups even for the same kind of thing.
5. Time NEVER separates records. Two records that match under rule 2 or rule 3 are one obligation even if their times disagree; the disagreement is reported elsewhere and is not your concern. Do not use time, zone, wording, phrasing, or which source a record came from as evidence that two records are different things.
6. Never group a todo or attention record with an event or appointment record.
7. A multi-day hotel stay appears as an all-day stay record and may also appear as a timed check-in record on its first day and a check-out record on its last day. The stay's start-day record and that day's check-in are the same obligation, with the check-in as primary; its last-day record and that day's check-out are the same obligation, with the check-out as primary. This holds even though identifiers differ; the relation is structural, not an identifier match.
8. WHEN UNCERTAIN, DO NOT MERGE. An unmerged duplicate costs one line; a wrong merge hides an obligation. This does not apply when rule 2 or rule 3 is satisfied - those are decisive, not judgment calls.
9. "primary": the member whose fields best render the line. For a journey group (rule 3), the member with the EARLIEST departure time. Otherwise prefer a record with a time over an all-day record; prefer the most specific summary. It must be one of the members.
10. "container": a trip. A trip is one named thing with a departure day and a return day. Give a container ONLY to groups on the trip's departure day (phase "depart") or return day (phase "return"). The name is "<destination> trip", where the destination is a city named in the records themselves. Never invent a name; if no destination is named, use null. Groups with no trip get null. The return day is the day of the flight home, or a hotel check-out, or the last day of a multi-day stay (its end_date). A group on that day that belongs to the same trip gets phase 'return'.

Records:
{records}
""".format(today=today.isoformat(), records=json.dumps(public, indent=1))


def _distinct_times(timed, by_id):
    """Count the distinct times a set of timed members actually carry.

    Ruled 2026-09-14: a time conflict is a disagreement about the instant,
    not the clock string. Calendar records carry a zone (build_records reads
    it off start_local); gmail commitments carry None, which means unknown,
    not local. So: members with a zone compare as UTC instants; a member
    without a zone agrees when its clock matches any zoned member's clock
    and otherwise counts as its own distinct time. The false positive this
    retires: the same flight stored as 19:30 America/New_York by one writer
    and 18:30 America/Chicago by another rendered as "sources disagree."
    Unparseable zones fall back to clock comparison rather than raising -
    this is a display decision, not validation.
    """
    instants, zoned_clocks, bare_clocks = set(), set(), set()
    for m in timed:
        r = by_id[m]
        clock, zone, d = r["time"], r.get("zone"), r.get("date")
        if zone and d:
            try:
                if isinstance(d, str):
                    d = datetime.fromisoformat(d).date()
                hh, mm = clock.split(":")
                local = datetime(d.year, d.month, d.day, int(hh), int(mm),
                                 tzinfo=ZoneInfo(zone))
                instants.add(local.astimezone(timezone.utc))
                zoned_clocks.add(clock)
                continue
            except (ValueError, KeyError, AttributeError):
                pass
        bare_clocks.add(clock)
    unresolved = {c for c in bare_clocks if c not in zoned_clocks}
    return len(instants) + len(unresolved)


def derive_conflicts(members, by_id):
    """A group's conflicts, computed from the records rather than reported.

    Ruled 2026-09-04: conflicts are code-derived. The model no longer sees the
    key, is no longer asked for it, and validate() ignores it if an older
    prompt or a stale reply still carries one. Reason: membership, primary and
    container proved stable across every live run at a fixed prompt, while
    conflicts moved on all three prompt versions - it was the one field asking
    for a judgment ("disagree materially") rather than a structure, which is
    where temperature 0 stops buying determinism. "summary" was the worst of
    it: two records describing one obligation always word it differently, so
    asking whether the difference matters is asking the model to re-litigate
    the merge it just made.

    Time is the only derived field. A time conflict exists when two or more
    members carry distinct non-null instants (see _distinct_times, ruled
    2026-09-14: zones are honored, a clock string is not an instant); its members are the ones that carry
    a time. An all-day record has no time to disagree with (ruled 2026-09-04),
    so it is never counted and never listed. Returns the group's complete
    conflicts list - empty when there is nothing to report.
    """
    timed = [m for m in members if by_id[m].get("time")]
    if _distinct_times(timed, by_id) < 2:
        return []
    return [{"field": "time", "members": timed}]


def filter_time_conflict(conflict, by_id):
    """Narrow or drop a time conflict so an all-day member cannot fake one.

    Render-side guard only. Since conflicts became code-derived (ruled
    2026-09-04) derive_conflicts satisfies this by construction, so validate()
    no longer calls it; it stands at the render in digest._conflict_text to
    defend a grouping that did not come through this module's validate() - a
    fixture recorded before the ruling, most of all.

    Ruled 2026-09-04: an all-day record has no time to disagree with. A time
    conflict counts only members that carry a time; if fewer than two distinct
    times remain, the conflict is dropped. A grouped all-day stay sitting
    beside its own timed check-in is the grouping working correctly, not two
    sources contradicting each other, and it must never render as one.

    Returns the conflict with its members narrowed to the timed ones, or None
    when it should not render at all. Non-time conflicts pass through
    untouched. Never raises - a dropped conflict is a display decision, not a
    validation failure.
    """
    if conflict["field"] != "time":
        return conflict
    timed = [m for m in conflict["members"] if by_id[m].get("time")]
    if _distinct_times(timed, by_id) < 2:
        return None
    return {"field": "time", "members": timed}


def validate(grouping, records):
    """Raise ValueError on any structural failure; return the normalized
    grouping otherwise. Code owns every check the model could get wrong.

    Conflicts are code-derived (ruled 2026-09-04). The model is not asked for
    them and a "conflicts" key in its reply is ignored rather than rejected -
    rejecting would fail the whole grouping into the S6 fallback and cost the
    morning over a field the model no longer owns. Each returned group carries
    derive_conflicts(members) instead."""
    by_id = {r["id"]: r for r in records}
    if not isinstance(grouping, dict) or not isinstance(grouping.get("groups"), list):
        raise ValueError("grouping is not an object with a groups list")
    seen = set()
    out = []
    for i, g in enumerate(grouping["groups"]):
        if not isinstance(g, dict):
            raise ValueError(f"group {i} is not an object")
        members = g.get("members")
        if not isinstance(members, list) or not members:
            raise ValueError(f"group {i} has no members list")
        for m in members:
            if m not in by_id:
                raise ValueError(f"group {i} names unknown id {m!r}")
            if m in seen:
                raise ValueError(f"id {m!r} appears in more than one group")
            seen.add(m)
        primary = g.get("primary")
        if primary not in members:
            raise ValueError(f"group {i} primary {primary!r} not in members")
        dates = {by_id[m]["date"] for m in members}
        if len(dates) > 1:
            raise ValueError(f"group {i} spans dates {sorted(map(str, dates))}")
        kinds = {by_id[m]["kind"] for m in members}
        if kinds & set(DO_KINDS) and kinds & set(HAPPEN_KINDS):
            raise ValueError(f"group {i} mixes do-kinds and happen-kinds {sorted(kinds)}")
        container = g.get("container")
        if container is not None:
            if (not isinstance(container, dict)
                    or not isinstance(container.get("name"), str)
                    or not container["name"].strip()
                    or container.get("phase") not in ("depart", "return")):
                raise ValueError(f"group {i} container malformed: {container!r}")
            container = {"name": container["name"].strip(), "phase": container["phase"]}
        # A model-emitted "conflicts" key is IGNORED, never rejected (ruled
        # 2026-09-04): an older prompt's habit must not cost the morning by
        # failing validation into the S6 fallback. Conflicts are derived below.
        out.append({
            "members": list(members),
            "primary": primary,
            "container": container,
            "conflicts": derive_conflicts(members, by_id),
        })
    # Canonical group order (ruled 2026-09-04): by lowest member id, so
    # stability is order-insensitive from here. The model returned the same
    # five groups in two different array orders across three runs; the page
    # was byte-identical either way, because the renderer sorts by timeline
    # and never by group position. Ordering here makes the RECORDED grouping
    # comparable too, so a real difference is the only thing a diff can show.
    order = {r["id"]: i for i, r in enumerate(records)}
    out.sort(key=lambda g: min(order[m] for m in g["members"]))
    missing = set(by_id) - seen
    if missing:
        raise ValueError(f"ids missing from grouping: {sorted(missing)}")
    return {"groups": out}


def _last_json_object(text):
    """(start, end) of the last complete top-level JSON object, or None.

    Anchored at the LAST "}" and searching backwards for the "{" that opens
    it, with json.loads as the balance test. Scanning from the end is what
    makes an unbalanced brace in the reasoning harmless: prose that says "the
    set {r1, r6 is one obligation" leaves an opening brace nothing ever
    closes, and a forward scan that tracks depth then never returns to zero
    and loses the real object entirely (measured 2026-09-04, which is why this
    is written from the end). Letting json.loads decide balance also means a
    brace inside a string value costs one rejected candidate instead of
    producing a wrong span, and a decoy - "group {r1, r6} together" ahead of
    the real object - is discarded because the search starts at the end.
    """
    end = text.rfind("}")
    if end == -1:
        return None
    i = text.rfind("{", 0, end + 1)
    while i != -1:
        try:
            json.loads(text[i:end + 1])
        except ValueError:
            i = text.rfind("{", 0, i)
            continue
        return i, end + 1
    return None


def _call_model(prompt):
    """One synthesis call. Temperature 0.

    THE REPLY CONTRACT (ruled 2026-09-04): reasoning is permitted first, and
    the JSON object comes last. The parser takes the last complete top-level
    object in the reply and ignores everything before it, logging SYNTHESIS
    PREAMBLE with the discarded length whenever anything precedes it. This is
    a contract the model demonstrably keeps, not one it is merely told to
    keep: on 2026-09-04 it began writing 1500-2400 characters of step-by-step
    prose ahead of the JSON on every run, and a prompt line forbidding a
    preamble did not stop it. A PREAMBLE line in digest.out is therefore
    expected, not an alarm - it measures how much reasoning the model wanted.

    Otherwise extract.py's house style: same client, same model, bare
    messages.create, strip-fence guard, JSON parse raising ValueError.

    WITHDRAWN WITH EVIDENCE (2026-09-04): an assistant prefill of "{" was
    ruled, built, and proven unimplementable on this model. The API rejects it
    outright - 400 invalid_request_error, "This model does not support
    assistant message prefill. The conversation must end with a user message."
    - three attempts on each of three runs, identical every time.

    FOLLOW-UP, not built: forced tool use / structured output is the way to
    make the shape enforced rather than parsed, and would retire this parser.
    It must be verified against the installed SDK (anthropic==0.112.0) and the
    model in MODEL before it is ruled, since the prefill ruling failed exactly
    there - on what this model accepts, not on what the idea was worth.
    extract.py shares this calling convention and stays a separate follow-up:
    it runs in production every morning, so a change there is its own verified
    change.
    """
    load_env_file()
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_output = response.content[0].text.strip()
    if raw_output.startswith("```"):
        raw_output = raw_output.split("\n", 1)[-1]
        if raw_output.endswith("```"):
            raw_output = raw_output[:-3]
        raw_output = raw_output.strip()
    span = _last_json_object(raw_output)
    if span is not None:
        start, end = span
        if start > 0:
            _log("PREAMBLE", {"chars": start})
        raw_output = raw_output[start:end]
    return raw_output


def synthesize(records, today):
    """Run the synthesis call over records. Returns (grouping, cause).

    grouping is the validated {"groups": [...]} or None. cause is None on
    success, "network" when every attempt failed to reach or get an answer
    from the API, or "validation" when the answer did not parse or did not
    pass validate(). Never raises: S6."""
    _log("INPUT", {"today": today.isoformat(), "records": records})
    if not records:
        _log("RESULT", {"groups": []})
        return {"groups": []}, None
    prompt = _prompt(records, today)
    raw = None
    last_error = None
    for attempt in range(RETRIES):
        try:
            raw = _call_model(prompt)
            break
        except Exception as error:
            last_error = error
            print(f"synthesize: attempt {attempt + 1} failed: {error}", file=sys.stderr)
            if attempt < RETRIES - 1:
                _time.sleep(BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)])
    if raw is None:
        _log("FALLBACK", {"cause": "network", "error": str(last_error)})
        return None, "network"
    _log("RAW", {"text": raw})
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        _log("FALLBACK", {"cause": "validation", "error": "not valid JSON"})
        return None, "validation"
    try:
        grouping = validate(parsed, records)
    except ValueError as error:
        _log("FALLBACK", {"cause": "validation", "error": str(error)})
        return None, "validation"
    _log("RESULT", grouping)
    return grouping, None
