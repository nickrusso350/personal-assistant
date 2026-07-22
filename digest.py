from datetime import date
from googleapiclient.discovery import build
from fetch_gmail import get_credentials, fetch_recent_messages
from fetch_calendar import fetch_upcoming_events
from extract import extract_commitments
from state import load_state, save_state, STATE_FILE

QUERY = "newer_than:2d"
MAX_RESULTS = 200
AGING_THRESHOLD_DAYS = 3

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

def format_date(value):
    """Humanize an ISO date string, e.g. '2026-08-06' -> 'Aug 6'.
    Routes through parse_iso_date; returns the raw value unchanged if unparseable."""
    if not value:
        return "no date"
    d = parse_iso_date(value)
    if d is None:
        return value
    return f"{d.strftime('%b')} {d.day}"

def detect_stalls(commitments, today):
    """Pure read over open commitments. Returns (overdue, aging).
    overdue: list of (commitment, days_past) for dated items past due.
    aging: list of (commitment, days_open) for DATELESS items open
    >= AGING_THRESHOLD_DAYS. Future-dated items get no nudge.
    Never mutates anything."""
    today_d = date.fromisoformat(today)
    overdue = []
    aging = []
    for c in commitments.values():
        if c["status"] != "open":
            continue
        d = parse_iso_date(c.get("date"))
        if d is not None:
            if d < today_d:
                overdue.append((c, (today_d - d).days))
        else:
            first_seen = parse_iso_date(c.get("first_seen"))
            if first_seen is None:
                continue
            days_open = (today_d - first_seen).days
            if days_open >= AGING_THRESHOLD_DAYS:
                aging.append((c, days_open))
    return overdue, aging

def derive_proposals(commitments, today):
    """Pure read over commitments. Returns the list of proposable commitments:
    status == "open", not yet calendared (no "calendar" key), a date that parses
    via parse_iso_date, and a parsed date that is today or later. Never mutates."""
    today_d = date.fromisoformat(today)
    proposals = []
    for c in commitments.values():
        if c["status"] != "open":
            continue
        if "calendar" in c:
            continue
        d = parse_iso_date(c.get("date"))
        if d is None:
            continue
        if d >= today_d:
            proposals.append(c)
    return proposals

def render_proposed(proposals):
    """Build proposable commitments as a list of lines, sorted by date.
    Informational — never mutates state and never shows commitment ids.
    Returns [] if there are no proposals."""
    if not proposals:
        return []
    items = sorted(proposals, key=lambda c: c["date"])
    lines = ["PROPOSED"]
    for c in items:
        when = format_date(c["date"])
        if c["time"]:
            when += f" at {c['time']}"
        lines.append(f"({c['type']}) {c['what']} — {when}")
    return lines

def render_stalls(overdue, aging):
    """Build the needs-attention block as a list of lines. Returns [] if both
    lists are empty."""
    if not overdue and not aging:
        return []
    lines = ["NEEDS ATTENTION"]
    for c, days_past in sorted(overdue, key=lambda t: -t[1]):
        plural = "day" if days_past == 1 else "days"
        lines.append(f"Overdue {days_past} {plural}: {c['what']} — was due {format_date(c['date'])}")
    for c, days_open in sorted(aging, key=lambda t: -t[1]):
        lines.append(f"Open {days_open} days, no date: {c['what']} — still on your plate?")
    return lines

def render_calendar(events):
    """Build upcoming calendar events as a list of lines. Purely informational
    — never touches state. Returns [] if there are no events."""
    if not events:
        return []

    def weekday_date(d):
        return f"{d.strftime('%a, %b')} {d.day}"

    lines = ["COMING UP"]
    for e in events:
        wd = weekday_date(e["start_local"])
        if e["all_day"]:
            lines.append(f"{wd} — all day — {e['summary']}")
        else:
            start = e["start_local"].strftime("%I:%M").lstrip("0")
            end = e["end_local"].strftime("%I:%M").lstrip("0")
            start_ampm = e["start_local"].strftime("%p")
            end_ampm = e["end_local"].strftime("%p")
            if start_ampm == end_ampm:
                timerange = f"{start}–{end} {end_ampm}"
            else:
                timerange = f"{start} {start_ampm}–{end} {end_ampm}"
            lines.append(f"{wd} — {timerange} — {e['summary']}")
    return lines

def render_digest(commitments, today, exclude_ids):
    """Build open commitments as a list of lines, sorted by date (undated last).
    Items in exclude_ids (already surfaced under NEEDS ATTENTION) are dropped.
    Never mutates status. Returns [] if nothing remains after filtering.
    """
    open_items = [
        c for c in commitments.values()
        if c["status"] == "open" and c["id"] not in exclude_ids
    ]
    open_items.sort(key=lambda c: c["date"] if c.get("date") else "9999-99-99")
    if not open_items:
        return []
    lines = ["COMMITMENTS"]
    for c in open_items:
        if c.get("date"):
            when = format_date(c["date"])
            if c["time"]:
                when += f" at {c['time']}"
        else:
            when = "no date"
        lines.append(f"({c['type']}) {c['what']} — {when}")
        if c["action_needed"]:
            lines.append(f"    Action: {c['action_needed']}")
        lines.append(f"    From: {c['sender']} — {c['subject']}")
    return lines

def build_digest(state, events, today):
    """Compose the full digest as a single string (excluding the run summary)."""
    d = parse_iso_date(today)
    header = f"Digest — {d.strftime('%a, %b')} {d.day}"
    overdue, aging = detect_stalls(state["commitments"], today)
    exclude_ids = {c["id"] for c, _ in overdue} | {c["id"] for c, _ in aging}
    stall_lines = render_stalls(overdue, aging)
    proposed_lines = render_proposed(derive_proposals(state["commitments"], today))
    commit_lines = render_digest(state["commitments"], today, exclude_ids)
    cal_lines = render_calendar(events)

    if not stall_lines and not proposed_lines and not commit_lines and not cal_lines:
        return header + "\n\nNothing needs your attention today."

    lines = [header]
    for section in (stall_lines, proposed_lines, commit_lines, cal_lines):
        if section:
            lines.append("")
            lines += section
    return "\n".join(lines)

def run_digest():
    """Run the full digest: fetch, process, render, and persist state.
    Prints the digest and run summary, and returns the composed digest string."""
    today = date.today().isoformat()

    state = load_state(STATE_FILE)

    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds)
    messages = fetch_recent_messages(service, QUERY, MAX_RESULTS)
    events = fetch_upcoming_events(7)

    skipped, extracted, failed = process_messages(messages, state, today)
    digest_text = build_digest(state, events, today)
    print(digest_text)

    save_state(state, STATE_FILE)

    open_total = sum(1 for c in state["commitments"].values() if c["status"] == "open")
    print(f"\n--- RUN SUMMARY ---")
    print(f"Messages fetched: {len(messages)}")
    print(f"Skipped (already processed / unreadable): {skipped}")
    print(f"Newly extracted: {extracted}")
    print(f"Extraction failures: {len(failed)}")
    for subj in failed:
        print(f"  - {subj}")
    print(f"Total open in state: {open_total}")

    return digest_text

if __name__ == "__main__":
    run_digest()
