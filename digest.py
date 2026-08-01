from datetime import date, datetime, time, timedelta
from googleapiclient.discovery import build
from fetch_gmail import get_credentials, fetch_recent_messages
from fetch_calendar import fetch_upcoming_events
from extract import extract_commitments
from reminders_write import reconcile, write_back
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

def render_attention(items):
    """Build the overdue block as a list of lines, sorted by date ascending.
    Every item here has a parseable date by construction. No day counts — the
    date says it. Returns [] if there is nothing overdue."""
    if not items:
        return []
    lines = ["NEEDS ATTENTION"]
    for c in sorted(items, key=lambda c: parse_iso_date(c["date"])):
        lines.append(f"{c['what']} — was due {format_date(c['date'])}")
    return lines

def render_todo(items):
    """Build the to-do block as a list of lines: dated items first, sorted by
    date ascending, then dateless items in their existing order. Returns [] if
    there is nothing to do."""
    if not items:
        return []
    dated = [c for c in items if parse_iso_date(c.get("date")) is not None]
    dateless = [c for c in items if parse_iso_date(c.get("date")) is None]
    dated.sort(key=lambda c: parse_iso_date(c["date"]))
    lines = ["TO DO"]
    for c in dated:
        lines.append(f"{c['what']} — due {format_date(c['date'])}")
    for c in dateless:
        lines.append(f"{c['what']}")
    return lines

def merge_coming_up(events, appointment_commitments):
    """Normalize calendar events and appointment commitments into one
    date-sorted timeline. Each item is {sort_key, label, summary}.

    sort_key's first element is always a date, never a datetime — comparing the
    two raises TypeError, and all-day events already carry plain dates."""
    merged = []
    for e in events:
        if e["all_day"]:
            d = e["start_local"]
            start_time = time.min
            label = f"{weekday_date(d)} — all day"
        else:
            d = e["start_local"].date()
            start_time = e["start_local"].time()
            start = e["start_local"].strftime("%I:%M").lstrip("0")
            end = e["end_local"].strftime("%I:%M").lstrip("0")
            start_ampm = e["start_local"].strftime("%p")
            end_ampm = e["end_local"].strftime("%p")
            if start_ampm == end_ampm:
                timerange = f"{start}–{end} {end_ampm}"
            else:
                timerange = f"{start} {start_ampm}–{end} {end_ampm}"
            label = f"{weekday_date(d)} — {timerange}"
        merged.append({
            "sort_key": (d, start_time),
            "label": label,
            "summary": e["summary"],
        })
    for c in appointment_commitments:
        d = parse_iso_date(c["date"])
        pretty = format_time(c.get("time"))
        label = f"{weekday_date(d)} — {pretty}" if pretty else weekday_date(d)
        try:
            start_time = datetime.strptime(c.get("time"), "%H:%M").time()
        except (TypeError, ValueError):
            # Unparseable or absent: sort it to the head of its day.
            start_time = time.min
        merged.append({
            "sort_key": (d, start_time),
            "label": label,
            "summary": c["what"],
        })
    merged.sort(key=lambda m: m["sort_key"])
    return merged

def render_coming_up(merged):
    """Build the merged timeline as a list of lines. Purely informational —
    never touches state. Returns [] if the timeline is empty."""
    if not merged:
        return []
    lines = ["COMING UP"]
    for m in merged:
        lines.append(f"{m['label']} — {m['summary']}")
    return lines

def build_digest(state, events, today, compact_calendar=False, partition=None):
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
    and renders strings, and never reaches Reminders."""
    d = parse_iso_date(today)
    title = f"Digest — {weekday_date(d)}"
    if partition is None:
        partition = partition_commitments(state["commitments"], d)
    attention, todo, appointments = partition
    merged = merge_coming_up(events, appointments)
    attention_lines = render_attention(attention)
    todo_lines = render_todo(todo)
    if compact_calendar:
        # Count the merged timeline, not events — appointments live there too.
        n = len(merged)
        noun = "event" if n == 1 else "events"
        cal_lines = [f"COMING UP: {n} {noun} in the next {HORIZON_DAYS} days"] if n else []
    else:
        cal_lines = render_coming_up(merged)

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
    attention, todo, _appointments = partition
    write_back(state, attention, todo)

    title, full_body = build_digest(state, events, today, partition=partition)
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
