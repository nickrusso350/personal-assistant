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

def render_stalls(overdue, aging):
    """Print the needs-attention block. Prints nothing if both lists are empty."""
    if not overdue and not aging:
        return
    print("\n--- NEEDS ATTENTION ---")
    for c, days_past in sorted(overdue, key=lambda t: -t[1]):
        plural = "day" if days_past == 1 else "days"
        print(f"[OVERDUE {days_past} {plural}] {c['what']} — was due {c['date']}")
    for c, days_open in sorted(aging, key=lambda t: -t[1]):
        print(f"[OPEN {days_open} days, no date] {c['what']} — still on your plate?")

def render_calendar(events):
    """Print upcoming calendar events. Purely informational — never touches
    state. Prints a placeholder line if there are no events."""
    print("--- COMING UP (next 7 days) ---")
    if not events:
        print("No upcoming events.")
        return
    for e in events:
        if e["all_day"]:
            when = e["start_local"].isoformat()
            time_range = "all day".ljust(11)
        else:
            when = e["start_local"].date().isoformat()
            time_range = f"{e['start_local'].strftime('%H:%M')}-{e['end_local'].strftime('%H:%M')}"
        print(f"{when}  {time_range}  {e['summary']}")

def render_digest(commitments, today):
    """Print all open commitments, sorted by date (undated last).
    Flags any open commitment whose date is before today as OVERDUE.
    Never mutates status.
    """
    open_items = [c for c in commitments.values() if c["status"] == "open"]
    open_items.sort(key=lambda c: c["date"] if c.get("date") else "9999-99-99")
    if not open_items:
        print("No open commitments.")
        return
    print("--- COMMITMENTS ---")
    for i, c in enumerate(open_items, 1):
        when = c["date"] or "no date"
        if c["time"]:
            when += f" at {c['time']}"
        overdue = c.get("date") and c["date"] < today
        flag = " [OVERDUE]" if overdue else ""
        print(f"[{i}] ({c['type']}) {c['what']} — {when}{flag}")
        if c["action_needed"]:
            print(f"    Action: {c['action_needed']}")
        print(f"    From: {c['sender']} — {c['subject']}")

if __name__ == "__main__":
    today = date.today().isoformat()
    print(f"Daily digest — {today}")
    print(f"Query: {QUERY}\n")

    state = load_state(STATE_FILE)

    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds)
    messages = fetch_recent_messages(service, QUERY, MAX_RESULTS)
    events = fetch_upcoming_events(7)

    skipped, extracted, failed = process_messages(messages, state, today)
    render_calendar(events)
    render_digest(state["commitments"], today)
    overdue, aging = detect_stalls(state["commitments"], today)
    render_stalls(overdue, aging)

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
