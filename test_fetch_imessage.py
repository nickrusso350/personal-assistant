"""Tests for fetch_imessage.fetch_messages on a self-authored chat.db.

Ruled 2026-10-10: the fixture database is built here, in a temp folder, with
the tables and columns fetch_imessage reads (message, handle, chat,
chat_handle_join, chat_message_join), and its blobs are authored with
attributed_body_fixtures.encode. The real chat.db is never read. Handles are
fictional 555-01xx numbers. The clock is pinned (NOW), never the wall clock.

Cases: (1) an allowlisted 1:1 chat returns both directions; (2) a chat with
one unlisted participant returns nothing and its blob never reaches decode,
and the allowlist is byte-exact (a space after the comma is part of the
handle); (3) the 48h boundary, 47h in and 49h out; (4) K=6 context out of ten
priors; (5) decode None skips the row with the exact log line and never falls
back to the text column; (6) a NULL blob uses the text column, both empty
skips silently; (7) the temp folder is gone after return and after an
exception; (8) the summary line's counts; (9) rows still in the -wal sidecar
are read; (10) an absent IMESSAGE_ALLOWLIST raises IMessageFetchError;
(11) a reaction (associated_message_type != 0) is neither a target nor
context, and its blob is never decoded; (12) a row empty after stripping
U+FFFC and whitespace is skipped silently and takes no context slot, while a
row with real text beside U+FFFC keeps its text unaltered; (13) a row whose
associated_message_type is NULL is returned as an ordinary message.

Run: python3 test_fetch_imessage.py
"""

import contextlib
import io
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

import fetch_imessage
from attributed_body_fixtures import encode
from fetch_imessage import IMessageFetchError, fetch_messages

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
A, B, X = "+15550100001", "+15550100002", "+15550100009"
ALLOW = [A, B]

SCHEMA = """
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL,
                     service TEXT);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL,
                   chat_identifier TEXT);
CREATE TABLE message (ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
                      guid TEXT UNIQUE NOT NULL, text TEXT, handle_id INTEGER DEFAULT 0,
                      attributedBody BLOB, date INTEGER, is_from_me INTEGER DEFAULT 0,
                      associated_message_type INTEGER DEFAULT 0);
CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER,
                                message_date INTEGER DEFAULT 0);
"""

# chat guid -> (participants,
#               [(guid, from_me, hours_ago, text_col, blob[, associated_message_type])])
CHATS = {
    "c1": ([A], [("g1a", 0, 2, None, encode("see you then")),
                 ("g1b", 1, 1, None, encode("yes"))]),
    "c2": ([A, X], [("g2", 0, 1, None, encode("out of scope"))]),
    "c3": ([A], [("g3out", 0, 49, None, encode("forty-nine")),
                 ("g3in", 0, 47, None, encode("forty-seven"))]),
    "c4": ([B], [(f"p{i}", i % 2, 101 - i, None, encode(f"prior {i}"))
                 for i in range(1, 11)]
                + [("g4", 0, 1, None, encode("target"))]),
    "c5": ([A], [("g5", 0, 1, "text column", b"not a typedstream")]),
    "c6": ([A], [("g6", 0, 1, "plain", None),
                 ("g6e", 0, 1, None, None)]),
    "c11": ([A], [("g11", 0, 1, None, encode("dinner works")),
                  ("g11r", 1, 0.75, None, encode("Loved \u201cdinner works\u201d"), 2000),
                  ("g11b", 1, 0.5, None, encode("see you"))]),
    "c12": ([A], [("g12a", 0, 1, None, encode("\ufffc")),
                  ("g12w", 0, 0.9, None, encode(" \ufffc\n")),
                  ("g12m", 0, 0.8, None, encode("look \ufffc")),
                  ("g12t", 0, 0.7, "\ufffc", None),
                  ("g12b", 1, 0.5, None, encode("nice"))]),
    "c13": ([A], [("g13", 0, 1, None, encode("null type"), None)]),
}


def provenance():
    """Instruments identify themselves (working rule, 2026-09-14)."""
    try:
        head = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=HERE, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = "(git unavailable)"
    print(f"host: {socket.gethostname()}")
    print(f"pwd:  {os.getcwd()}")
    print(f"head: {head}\n")


def ns(hours_ago):
    return int((NOW - timedelta(hours=hours_ago) - EPOCH).total_seconds()) * 10**9


def populate(conn, chats):
    conn.executescript(SCHEMA)
    handles = {}
    for chat_guid, (people, messages) in chats.items():
        chat_id = conn.execute("INSERT INTO chat (guid, chat_identifier) VALUES (?, ?)",
                               (chat_guid, chat_guid)).lastrowid
        for h in people:
            if h not in handles:
                handles[h] = conn.execute("INSERT INTO handle (id, service) VALUES (?, 'iMessage')",
                                          (h,)).lastrowid
            conn.execute("INSERT INTO chat_handle_join VALUES (?, ?)", (chat_id, handles[h]))
        for guid, from_me, hours, text, blob, *assoc in messages:
            mid = conn.execute(
                "INSERT INTO message (guid, text, handle_id, attributedBody, date, is_from_me, "
                "associated_message_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (guid, text, 0 if from_me else handles[people[0]], blob, ns(hours),
                 from_me, assoc[0] if assoc else 0)).lastrowid
            conn.execute("INSERT INTO chat_message_join VALUES (?, ?, ?)",
                         (chat_id, mid, ns(hours)))
    conn.commit()


def build(chats=CHATS):
    src = tempfile.mkdtemp(prefix="fixture_messages_")
    conn = sqlite3.connect(os.path.join(src, "chat.db"))
    populate(conn, chats)
    conn.close()
    return src


def run(src, **kw):
    """fetch_messages with decode counted and stdout captured."""
    seen_blobs, real = [], fetch_imessage.decode

    def counting(blob):
        seen_blobs.append(blob)
        return real(blob)

    fetch_imessage.decode = counting
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            records = fetch_messages(now=NOW, source_dir=src, **{"allowlist": ALLOW, **kw})
    finally:
        fetch_imessage.decode = real
    return records, out.getvalue().splitlines(), seen_blobs


def by_guid(records):
    return {r["guid"]: r for r in records}


def main():
    provenance()
    src = build()
    try:
        records, lines, blobs = run(src)
        got = by_guid(records)

        # (1) allowlisted 1:1, both directions
        c1 = [r for r in records if r["chat"] == "c1"]
        assert [r["guid"] for r in c1] == ["g1a", "g1b"], c1
        assert (got["g1a"]["sender"], got["g1a"]["is_from_me"]) == (A, False)
        assert (got["g1b"]["sender"], got["g1b"]["is_from_me"]) == ("me", True)
        assert got["g1b"]["text"] == "yes"
        assert got["g1b"]["context"] == [
            {"sender": A, "ts": got["g1a"]["ts"], "text": "see you then", "is_from_me": False}]
        assert datetime.fromisoformat(got["g1a"]["ts"]).utcoffset() is not None
        print("PASS 1 allowlisted 1:1 returns both directions")

        # (2) one unlisted participant: nothing returned, blob never decoded
        assert not [r for r in records if r["chat"] == "c2"]
        assert encode("out of scope") not in blobs
        spaced, _, spaced_blobs = run(src, allowlist=f"{A}, {B}".split(","))
        assert not [r for r in spaced if r["chat"] == "c4"], "space must not be stripped"
        assert encode("target") not in spaced_blobs
        print("PASS 2 unlisted participant: no records, no decode; byte-exact allowlist")

        # (3) 48h boundary
        assert "g3in" in got and "g3out" not in got
        assert [c["text"] for c in got["g3in"]["context"]] == ["forty-nine"]
        print("PASS 3 window: 47h in, 49h out")

        # (4) K=6 out of ten priors, oldest first
        ctx = got["g4"]["context"]
        assert [c["text"] for c in ctx] == [f"prior {i}" for i in range(5, 11)], ctx
        assert all(set(c) == {"sender", "ts", "text", "is_from_me"} for c in ctx)
        assert [c["sender"] for c in ctx] == ["me" if i % 2 else B for i in range(5, 11)]
        assert encode("prior 4") not in blobs, "context read past K"
        print("PASS 4 context: exactly the 6 nearest priors")

        # (5) decode None: skipped, exact line, no text-column fallback
        assert "g5" not in got
        assert lines.count("IMESSAGE DECODE failed guid=g5") == 1, lines
        assert not any("text column" in str(r) for r in records)
        print("PASS 5 decode None: skipped with the exact log line")

        # (6) NULL blob -> text column; both empty -> silent skip
        assert got["g6"]["text"] == "plain"
        assert "g6e" not in got and not any("g6e" in line for line in lines)
        print("PASS 6 NULL blob uses text column; empty row skipped silently")

        # (11) reaction: not a target, not context, never decoded
        assert "g11r" not in got
        assert [c["text"] for c in got["g11b"]["context"]] == ["dinner works"]
        assert encode("Loved \u201cdinner works\u201d") not in blobs
        print("PASS 11 reaction excluded from targets and context, never decoded")

        # (12) attachment-only: silent skip, no context slot; mixed text kept
        assert not {"g12a", "g12w", "g12t"} & set(got)
        assert got["g12m"]["text"] == "look \ufffc"
        assert [c["text"] for c in got["g12b"]["context"]] == ["look \ufffc"]
        assert not any("g12" in line for line in lines)
        print("PASS 12 attachment-only skipped silently; real text kept unaltered")

        # (13) NULL associated_message_type: an ordinary message
        check = sqlite3.connect(os.path.join(src, "chat.db"))
        try:
            stored = check.execute("SELECT associated_message_type FROM message "
                                   "WHERE guid = 'g13'").fetchone()
        finally:
            check.close()
        assert stored == (None,), stored
        assert got["g13"]["text"] == "null type"
        assert (got["g13"]["sender"], got["g13"]["is_from_me"]) == (A, False)
        print("PASS 13 NULL associated_message_type returned as an ordinary message")

        # (8) summary counts: c1 2, c3 1, c4 1, c5 0 (touched), c6 1, c11 2,
        # c12 2, c13 1; c2 out
        assert len(records) == 10
        assert lines == ["IMESSAGE DECODE failed guid=g5",
                         "IMESSAGE FETCH 10 messages from 8 allowlisted chats"], lines
        print("PASS 8 summary line counts")

        # (7) temp folder removed on success and on exception
        made, real_mkdtemp = [], tempfile.mkdtemp

        def recording(*a, **kw):
            made.append(real_mkdtemp(*a, **kw))
            return made[-1]

        fetch_imessage.tempfile.mkdtemp = recording
        try:
            run(src)
            empty = real_mkdtemp(prefix="fixture_empty_")
            try:
                run(empty)
                raise AssertionError("missing chat.db did not raise")
            except IMessageFetchError as e:
                assert e.errno == 2, e.errno
            finally:
                shutil.rmtree(empty)
        finally:
            fetch_imessage.tempfile.mkdtemp = real_mkdtemp
        assert len(made) == 2 and not any(os.path.exists(p) for p in made), made
        print("PASS 7 temp folder gone after success and after exception")
    finally:
        shutil.rmtree(src)

    # (9) rows still in the -wal sidecar are read from the copy
    wal_src = tempfile.mkdtemp(prefix="fixture_wal_")
    writer = sqlite3.connect(os.path.join(wal_src, "chat.db"))
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        populate(writer, {"c9": ([A], [("g9", 0, 1, None, encode("in the wal"))])})
        assert os.path.getsize(os.path.join(wal_src, "chat.db-wal")) > 0
        records, lines, _ = run(wal_src)
        assert [r["text"] for r in records] == ["in the wal"], records
        print("PASS 9 rows in the -wal sidecar are read")
    finally:
        writer.close()
        shutil.rmtree(wal_src)

    # (10) absent allowlist raises, never reads as no chats
    real_load, saved = fetch_imessage.load_env_file, os.environ.pop("IMESSAGE_ALLOWLIST", None)
    fetch_imessage.load_env_file = lambda: None
    try:
        fetch_messages(now=NOW, source_dir=HERE)
        raise AssertionError("absent allowlist did not raise")
    except IMessageFetchError as e:
        assert str(e) == "IMESSAGE_ALLOWLIST is not set"
    finally:
        fetch_imessage.load_env_file = real_load
        if saved is not None:
            os.environ["IMESSAGE_ALLOWLIST"] = saved
    print("PASS 10 absent allowlist raises IMessageFetchError")

    print("\nALL PASS (13 cases)")


if __name__ == "__main__":
    main()
