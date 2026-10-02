"""Watchdog for the daily digest.

Runs as its own LaunchAgent (com.nickrusso.dailydigest.watchdog) at 7:30,
after the 7:00 digest should have finished. It reads the digest's stdout log
and posts at most one Pushover request. It touches no Google API, no
Reminders, and no state.json, so it needs no OAuth token and no Automation
grant. Its whole surface is: Python runs, the log is readable, the network
reaches Pushover.

What it checks: the last RUN START line in digest.out, which must carry
today's local date, followed in the same run by PRE-SEND and then
"Sent via Pushover". Those three lines are matched byte-for-byte.

    digest.out today                      class                     result
    ------------------------------------  ------------------------  ------
    file missing or unreadable            log absent                page
    no RUN START                          spawn refused             page
    RUN START, no PRE-SEND                crashed before send       page
    PRE-SEND, no "Sent via Pushover"      send not confirmed        page
    all three                             delivered                 silent

On Sundays a heartbeat page is sent regardless, carrying the count of
paired mornings over the last seven days, so that silence on the other six
days reads as "fine" rather than "dead". A Sunday failure folds the count
into the failure page: one request per run, always.

What this cannot catch (stated so nobody assumes otherwise): the watchdog
not being spawned at all (a shared cause refuses both agents), the network
being down at 7:30 as it was at 7:00, and the mini being off. The Sunday
heartbeat is what makes the first of these visible, weekly, to a person.

Pages state what was observed. They do not instruct.
"""

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from env_loader import load_env_file

LOG_PATH = Path.home() / "Library" / "Logs" / "personal_assistant" / "digest.out"
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
TITLE = "Digest watchdog"
ATTEMPTS = 3
RETRY_WAIT_SECONDS = 60
HEARTBEAT_WEEKDAY = 6  # Sunday
WINDOW_DAYS = 7

RUN_START = "RUN START "
PRE_SEND = "PRE-SEND "
SENT = "Sent via Pushover"


def stamp():
    return datetime.now().isoformat(timespec="seconds")


def read_runs(log_path):
    """Return the log as a list of runs, oldest first. Each run is a dict
    with the RUN START stamp's date prefix (YYYY-MM-DD), and whether a
    PRE-SEND and a "Sent via Pushover" line followed it before the next
    RUN START. Raises OSError if the file cannot be read."""
    runs = []
    current = None
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith(RUN_START):
                current = {
                    "date": line[len(RUN_START):len(RUN_START) + 10],
                    "presend": False,
                    "sent": False,
                }
                runs.append(current)
            elif current is None:
                continue
            elif line.startswith(PRE_SEND):
                current["presend"] = True
            elif line.startswith(SENT):
                current["sent"] = True
    return runs


def classify(runs, today):
    """Return (ok, description) for today's last run."""
    last = runs[-1] if runs else None
    if last is None or last["date"] != today:
        return False, f"no RUN START for {today}"
    if not last["presend"]:
        return False, f"run started {today}, did not reach send"
    if not last["sent"]:
        return False, f"run reached send {today}, delivery not confirmed"
    return True, f"paired {today}"


def paired_count(runs, today):
    """Count days in the last WINDOW_DAYS (ending today) whose last run has
    all three lines."""
    last_by_date = {}
    for run in runs:
        last_by_date[run["date"]] = run
    end = date.fromisoformat(today)
    count = 0
    for offset in range(WINDOW_DAYS):
        day = (end - timedelta(days=offset)).isoformat()
        run = last_by_date.get(day)
        if run and run["presend"] and run["sent"]:
            count += 1
    return count


def post(message, priority, dry_run):
    """Post one Pushover message, retrying a bounded number of times.
    Raises RuntimeError after the last attempt fails. Credentials are read
    only when a real send is about to happen."""
    if dry_run:
        print(f"DRY-RUN priority={priority} message={message!r}")
        return
    load_env_file()
    user = os.environ.get("PUSHOVER_USER_KEY")
    if not user:
        raise RuntimeError("Missing environment variable: PUSHOVER_USER_KEY")
    token = os.environ.get("PUSHOVER_WATCHDOG_TOKEN")
    if not token:
        raise RuntimeError("Missing environment variable: PUSHOVER_WATCHDOG_TOKEN")
    data = {
        "token": token,
        "user": user,
        "title": TITLE,
        "message": message,
        "priority": priority,
    }
    last_error = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = requests.post(PUSHOVER_URL, data=data, timeout=15)
            response.raise_for_status()
            if response.json().get("status") != 1:
                raise RuntimeError(f"Pushover rejected: {response.text}")
            return
        except Exception as e:  # noqa: BLE001 - every failure is retried, bounded
            last_error = e
            print(f"WATCHDOG SEND ATTEMPT {attempt}/{ATTEMPTS} failed: {e}")
            if attempt < ATTEMPTS:
                time.sleep(RETRY_WAIT_SECONDS)
    raise RuntimeError(f"Pushover unreachable after {ATTEMPTS} attempts: {last_error}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", default=str(LOG_PATH),
                        help="digest.out to read (fixtures only; default is the live log)")
    parser.add_argument("--date", default=None,
                        help="treat this YYYY-MM-DD as today (fixtures only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be sent instead of sending")
    args = parser.parse_args(argv)

    today = args.date or date.today().isoformat()
    heartbeat = date.fromisoformat(today).weekday() == HEARTBEAT_WEEKDAY
    print(f"WATCHDOG START {stamp()} today={today} log={args.log}")

    try:
        runs = read_runs(args.log)
    except OSError as e:
        ok, description, runs = False, f"digest.out missing or unreadable: {e}", []
    else:
        ok, description = classify(runs, today)

    message = None
    priority = 0
    if not ok:
        message = f"{today}: {description}."
        priority = 1
    if heartbeat:
        count = paired_count(runs, today)
        week = f"{count} of {WINDOW_DAYS} mornings paired through {today}"
        message = f"{message} {week}." if message else f"Heartbeat: {week}."

    if message is None:
        print(f"WATCHDOG OK {stamp()} {description}")
        return 0

    try:
        post(message, priority, args.dry_run)
    except RuntimeError as e:
        print(f"WATCHDOG FAILED {stamp()} {description}; {e}")
        return 1
    print(f"WATCHDOG {'OK' if ok else 'PAGED'} {stamp()} {description}; sent priority {priority}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
