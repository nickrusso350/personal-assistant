from datetime import date
from googleapiclient.discovery import build
from fetch_gmail import get_credentials, fetch_recent_messages
from extract import extract_commitments

QUERY = "newer_than:2d"
MAX_RESULTS = 25

def build_digest(messages):
    """Run the extraction brain over fetched messages.
    Returns (commitments, skipped_count, failed_subjects).
    """
    commitments = []
    skipped = 0
    failed = []
    for msg in messages:
        if msg["body"] is None:
            skipped += 1
            continue
        try:
            results = extract_commitments(msg["body"])
        except Exception:
            failed.append(msg["subject"] or "(no subject)")
            continue
        # extract_commitments returns a list; fold each commitment in,
        # stamping the source message onto every one. An empty list adds
        # nothing, so messages with no commitment simply contribute nothing.
        for commitment in results:
            commitment["subject"] = msg["subject"]
            commitment["sender"] = msg["sender"]
            commitments.append(commitment)
    # Sort by date ascending; undated commitments go last.
    commitments.sort(key=lambda c: c["date"] if c.get("date") else "9999-99-99")
    return commitments, skipped, failed

if __name__ == "__main__":
    print(f"Daily digest — {date.today().isoformat()}")
    print(f"Query: {QUERY}\n")
    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds)
    messages = fetch_recent_messages(service, QUERY, MAX_RESULTS)
    if not messages:
        print("No messages found in this window. Nothing to digest.")
        raise SystemExit(0)
    commitments, skipped, failed = build_digest(messages)
    if commitments:
        print("--- COMMITMENTS ---")
        for i, c in enumerate(commitments, 1):
            when = c["date"] or "no date"
            if c["time"]:
                when += f" at {c['time']}"
            print(f"[{i}] ({c['type']}) {c['what']} — {when}")
            if c["action_needed"]:
                print(f"    Action: {c['action_needed']}")
            print(f"    From: {c['sender']} — {c['subject']}")
    else:
        print("No commitments found in this window.")
    print(f"\n--- RUN SUMMARY ---")
    print(f"Messages fetched: {len(messages)}")
    print(f"Commitments found: {len(commitments)}")
    print(f"Unreadable (skipped): {skipped}")
    if failed:
        print(f"Extraction failures: {len(failed)}")
        for subj in failed:
            print(f"  - {subj}")
