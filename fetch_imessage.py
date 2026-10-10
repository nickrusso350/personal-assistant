"""Fetch recent text messages from allowlisted chats. Read-only by construction.

Written 2026-10-10 to the iMessage-as-source rulings (2026-10-04, 2026-10-08,
2026-10-10). NOT on the 07:00 path: nothing the scheduled run imports imports
this module until the wiring step is ruled and proven.

Read-only boundary (ruled 2026-10-04). The live database is never opened.
chat.db and its -wal and -shm sidecars are copied from ~/Library/Messages into
a tempfile.mkdtemp() folder, the COPY is opened with sqlite URI mode=ro, and
the folder is deleted in a finally block. The -wal copy is what makes the
snapshot complete: Messages keeps its newest rows there until checkpoint. A
sidecar absent at copy time is skipped (nothing uncheckpointed to read);
chat.db itself absent is a fetch failure.

Scope (ruled 2026-10-04). IMESSAGE_ALLOWLIST in ~/.personal_assistant.env is a
comma-separated list of handles, compared byte-exact against handle.id: no
normalization, no stripping beyond the comma split. A chat is in scope when it
has at least one participant in chat_handle_join and every one of them is
allowlisted (the account owner is never listed there). Both directions within
an in-scope chat are read. The gate runs in SQL before any row is decoded: an
out-of-scope chat's blobs are never selected, so never decoded. An absent
allowlist raises IMessageFetchError; it never reads as "no chats".

Identity. The native id is message.guid, never ROWID (ROWID is local to one
database file). ROWID appears here only as an ordering tiebreak between rows
with equal dates; it is never emitted.

Window (ruled 2026-10-08, provisional). T = 48 hours: a target is a message
whose send time is at or after now - 48h. K = 6: each target carries the six
nearest earlier messages in the same chat that yield text, any age, oldest
first. A row that yields no text (empty, or a failed decode) does not take a
context slot.

Decode rule (ruled 2026-10-08, 2026-10-10). attributedBody not NULL ->
attributed_body.decode(blob); None -> print IMESSAGE DECODE failed guid=<guid>
and skip the row, never falling back to the text column. attributedBody NULL
-> the text column. Both empty -> skip silently. Each row is decoded at most
once per run, so a failed row logs once even when it sits in several windows.

Reactions and attachments (ruled 2026-10-10). Reactions (tapbacks) are
excluded in SQL by COALESCE(associated_message_type, 0) = 0, on targets and
context alike; a NULL there is an ordinary message, not a reaction.
A row whose text, from either source, is empty once U+FFFC (the attachment
placeholder) and whitespace are stripped is skipped silently, like an empty
row; it takes no context slot. A row that keeps real text keeps it unaltered.

Record shape (ruled 2026-10-10), one dict per target:
    {"guid": str, "chat": str (chat.guid), "sender": "me" | handle.id,
     "is_from_me": bool, "ts": ISO 8601 local with offset, "text": str,
     "context": [{"sender", "ts", "text", "is_from_me"}, ...]}
extract_text_commitments(message, sender, anchor_ts, context) takes text,
sender, datetime.fromisoformat(ts) and context; the handle never leaves this
record, and only _labels output reaches the prompt.

Logging is print to stdout, the repo's convention (ruled 2026-10-10): one
IMESSAGE FETCH summary line on success, IMESSAGE DECODE lines carrying guids.
Message text is never printed. Failure on the copy/open/read path raises
IMessageFetchError; the caller owns the catch and the IMESSAGE FETCH FAILED
line.

Run: python3 fetch_imessage.py --dry-run   (needs Full Disk Access)
"""
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from attributed_body import decode
from env_loader import load_env_file

SOURCE_DIR = os.path.expanduser("~/Library/Messages")
DB_FILES = ("chat.db", "chat.db-wal", "chat.db-shm")
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
T_HOURS = 48          # ruled 2026-10-08, provisional
K = 6                 # ruled 2026-10-08, provisional
ATTACHMENT = "\ufffc"  # object replacement character: an attachment's placeholder

ROW_COLUMNS = """m.ROWID, m.guid, m.is_from_me, h.id, m.date, m.text,
       m.attributedBody"""
ROW_FROM = """FROM chat_message_join cmj
JOIN message m ON m.ROWID = cmj.message_id
LEFT JOIN handle h ON h.ROWID = m.handle_id"""


class IMessageFetchError(Exception):
    """Any failure on the copy/open/read path. errno is the underlying OS
    errno when there is one, else None."""

    def __init__(self, message, errno=None):
        super().__init__(message)
        self.errno = errno


def _ts(raw):
    """message.date -> ISO 8601 local time with offset. Current macOS stores
    nanoseconds since 2001-01-01 UTC; older rows hold seconds."""
    seconds = raw / 1e9 if abs(raw) > 1e11 else raw
    local = (APPLE_EPOCH + timedelta(seconds=seconds)).astimezone()
    return local.isoformat(timespec="seconds")


def _allowlist():
    load_env_file()
    raw = os.environ.get("IMESSAGE_ALLOWLIST")
    if raw is None:
        raise IMessageFetchError("IMESSAGE_ALLOWLIST is not set")
    return raw.split(",")


def _in_scope_chats(conn, allowlist):
    """chat ROWID -> chat.guid for chats whose every participant is listed."""
    rows = conn.execute("""
        SELECT c.ROWID, c.guid, h.id
        FROM chat c
        JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
        JOIN handle h ON h.ROWID = chj.handle_id""").fetchall()
    allowed = set(allowlist)
    guids, ok = {}, {}
    for chat_id, chat_guid, handle in rows:
        guids[chat_id] = chat_guid
        ok[chat_id] = ok.get(chat_id, True) and handle in allowed
    return {cid: guids[cid] for cid, good in ok.items() if good}


class _Decoder:
    """Applies the decode rule once per guid per run."""

    def __init__(self):
        self.cache = {}

    def text(self, guid, body_text, blob):
        if guid not in self.cache:
            if blob is not None:
                got = decode(blob)
                if got is None:
                    print(f"IMESSAGE DECODE failed guid={guid}")
            else:
                got = body_text
            if got is not None and not got.replace(ATTACHMENT, "").strip():
                got = None                    # empty or attachment-only: silent
            self.cache[guid] = got
        return self.cache[guid]


def _entry(row, text):
    _rowid, guid, from_me, handle, raw, _t, _b = row
    return {"sender": "me" if from_me else handle, "ts": _ts(raw),
            "text": text, "is_from_me": bool(from_me)}


def _read_chat(conn, chat_id, chat_guid, cutoff, k, decoder, seen):
    """Targets for one chat, or None when the chat has no row in the window."""
    window = conn.execute(
        f"SELECT {ROW_COLUMNS} {ROW_FROM} WHERE COALESCE(m.associated_message_type, 0) = 0 "
        "AND cmj.chat_id = ? AND m.date >= ? "
        "ORDER BY m.date, m.ROWID", (chat_id, cutoff)).fetchall()
    if not window:
        return None
    first = window[0]
    earlier = conn.execute(
        f"SELECT {ROW_COLUMNS} {ROW_FROM} WHERE COALESCE(m.associated_message_type, 0) = 0 "
        "AND cmj.chat_id = ? "
        "AND (m.date < ? OR (m.date = ? AND m.ROWID < ?)) "
        "ORDER BY m.date DESC, m.ROWID DESC", (chat_id, first[4], first[4], first[0]))
    prior = []
    for row in earlier:                       # lazily, newest first, until K
        text = decoder.text(row[1], row[5], row[6])
        if text is not None:
            prior.append(_entry(row, text))
            if len(prior) == k:
                break
    history = prior[::-1]
    records = []
    for row in window:
        text = decoder.text(row[1], row[5], row[6])
        if text is None:
            continue
        entry = _entry(row, text)
        if row[1] not in seen:
            seen.add(row[1])
            records.append({"guid": row[1], "chat": chat_guid,
                            "sender": entry["sender"],
                            "is_from_me": entry["is_from_me"],
                            "ts": entry["ts"], "text": text,
                            "context": [dict(e) for e in history[-k:]] if k else []})
        history.append(entry)
    return records


def fetch_messages(now=None, allowlist=None, source_dir=SOURCE_DIR,
                   hours=T_HOURS, k=K):
    """Return the window's text messages from allowlisted chats, oldest first
    within each chat. Prints one IMESSAGE FETCH summary line on success.
    Raises IMessageFetchError on any copy/open/read failure."""
    if allowlist is None:
        allowlist = _allowlist()
    now = now or datetime.now(timezone.utc)
    cutoff = int((now - timedelta(hours=hours) - APPLE_EPOCH).total_seconds()) * 10**9
    tmp = tempfile.mkdtemp(prefix="imessage_")
    try:
        for name in DB_FILES:
            src = os.path.join(source_dir, name)
            if name == "chat.db" or os.path.exists(src):
                shutil.copy2(src, os.path.join(tmp, name))
        conn = sqlite3.connect(f"file:{os.path.join(tmp, 'chat.db')}?mode=ro", uri=True)
        try:
            decoder, seen, records, touched = _Decoder(), set(), [], 0
            for chat_id, chat_guid in sorted(_in_scope_chats(conn, allowlist).items()):
                got = _read_chat(conn, chat_id, chat_guid, cutoff, k, decoder, seen)
                if got is not None:
                    touched += 1
                    records.extend(got)
        finally:
            conn.close()
    except IMessageFetchError:
        raise
    except Exception as e:
        raise IMessageFetchError(f"{type(e).__name__}: {e}",
                                 getattr(e, "errno", None)) from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"IMESSAGE FETCH {len(records)} messages from {touched} allowlisted chats")
    return records


def provenance():
    """Instruments identify themselves (working rule, 2026-09-14)."""
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        head = subprocess.run(["git", "log", "-1", "--oneline"], cwd=here,
                              capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = "(git unavailable)"
    print(f"host: {socket.gethostname()}")
    print(f"pwd:  {os.getcwd()}")
    print(f"head: {head}\n")


def main():
    """--dry-run: the real fetch, printing only the provenance header, the
    summary line and any DECODE-failed guids. Records are discarded."""
    if sys.argv[1:] != ["--dry-run"]:
        print("usage: python3 fetch_imessage.py --dry-run")
        sys.exit(2)
    provenance()
    try:
        fetch_messages()
    except IMessageFetchError as e:
        print(f"IMESSAGE FETCH FAILED {e.errno if e.errno is not None else e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
