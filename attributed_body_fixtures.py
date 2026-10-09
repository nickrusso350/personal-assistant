#!/usr/bin/env python3
"""Prove attributed_body.decode on self-authored fixtures, and against chat.db ground truth.

Written 2026-10-09 for the iMessage-as-source build (decoder ruled 2026-10-08,
layout ruled 2026-10-09; see attributed_body.py). Explicit invocation only.

Two modes:
  FIXTURES (default) - $0, offline. encode() below authors blobs in the
    measured layout; fixtures are never real blobs (ruled 2026-10-08).
    Positives (decode must return the exact text):
      lengths 5, 127, 128, 300 and 70,000 bytes - both sides of each length
        form; 70,000 exercises 0x82 + uint32 LE, which is encoder-only
        (never observed in real rows);
      a multibyte string (the length prefix counts UTF-8 bytes);
      the 0x94 and 0x95 introducers, both observed in real rows.
    Each positive also reads the length tag byte out of the encoded blob and
      checks it against the form written beside the fixture.
    Negatives (decode must return None): random garbage, a blob truncated
      inside the string, a blob with a wrong trailer.

  --ground-truth PATH [--since-days N] - opens the SQLite copy at PATH
    read-only (?mode=ro; never ~/Library/Messages) and walks the dual-form
    rows, those holding both `text` and `attributedBody`. Each row is either
    skipped for a named reason or compared: decode(attributedBody) encoded
    as UTF-8 against the raw bytes of `text`. Prints three counts - matched,
    mismatched, skipped - and the skip breakdown. Never prints row content.
      Gate (ruled 2026-10-09): --since-days 30, 0 mismatches.
      Without --since-days: every dual-form row, informational only.
    Skip reasons partition exactly (ruled 2026-10-09), first match wins:
      tapback          associated_message_type != 0;
      attachment-only  text is exactly U+FFFC, and not a tapback;
      empty            text is '', and neither of the above.
    The tapback / attachment-only overlap is measured and printed beside
    the breakdown, so the partition can be checked against the raw counts.
    A decode returning None on a compared row counts as a mismatch.
    Each mismatch prints one structural line only (ruled 2026-10-09):
    encoded length vs text byte length (equal / not / unparsed), trailer
    present (yes / no), and the row's year.

Exit 0 only when every fixture passes, or the ground truth has 0 mismatches.
Writes nothing.

Usage:
  python3 attributed_body_fixtures.py
  python3 attributed_body_fixtures.py --ground-truth PATH [--since-days N]
"""
import argparse
import os
import random
import socket
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REPO)

from attributed_body import MARKER, TRAILER, decode            # noqa: E402

# Self-authored preamble and tail around the string; decode() reads neither.
PREAMBLE = b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84"
TAIL = b"\x02iI\x01\x86"
APPLE_EPOCH = 978307200
ATTACHMENT = "￼".encode("utf-8")


def provenance():
    """Instruments identify themselves (working rule, 2026-09-14)."""
    try:
        head = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=REPO, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        head = "(git unavailable)"
    print(f"host: {socket.gethostname()}")
    print(f"pwd:  {os.getcwd()}")
    print(f"head: {head}\n")


def _length_field(n):
    """The three length forms (ruled 2026-10-09)."""
    if n <= 0x7F:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\x81" + n.to_bytes(2, "little")
    return b"\x82" + n.to_bytes(4, "little")


def encode(text, introducer=0x94):
    """Author a blob in the measured layout. For fixtures only."""
    body = text.encode("utf-8")
    return (PREAMBLE + MARKER + bytes([0x01, introducer]) + b"\x84\x01\x2b"
            + _length_field(len(body)) + body + TRAILER + TAIL)


def _tag_form(tag):
    """Name the length form from the tag byte actually in the blob."""
    if tag <= 0x7F:
        return "1-byte"
    return {0x81: "0x81+u16", 0x82: "0x82+u32"}.get(tag, f"tag {tag:#04x}")


def fixtures():
    lines, all_ok = [], True
    positives = [(f"len {n}", "x" * n, 0x94, form) for n, form in (
        (5, "1-byte"), (127, "1-byte"), (128, "0x81+u16"),
        (300, "0x81+u16"), (70000, "0x82+u32"))]
    multibyte = "Café \U0001f44b\U0001f3fd — ok"
    positives += [
        ("multibyte", multibyte, 0x94, "1-byte"),
        ("introducer 0x94", "see you there", 0x94, "1-byte"),
        ("introducer 0x95", "see you there", 0x95, "1-byte"),
    ]
    for name, text, intro, form in positives:
        blob = encode(text, intro)
        got = decode(blob)
        n = len(text.encode("utf-8"))
        actual = _tag_form(blob[blob.index(MARKER) + len(MARKER) + 5])  # tag as encoded
        ok = got == text and actual == form
        note = " encoder-only" if form == "0x82+u32" else ""
        extra = f" chars={len(text)}" if len(text) != n else ""
        lines.append(f"fixture {name:<16} {'ok  ' if ok else 'FAIL'} bytes={n}{extra} "
                     f"form={actual}{note} intro={intro:#04x}")
        all_ok &= ok

    good = encode("x" * 300)
    body_at = good.index(MARKER) + len(MARKER) + 5 + 3
    negatives = [
        ("garbage", random.Random(20261009).randbytes(256)),
        ("truncated", good[:body_at + 150]),
        ("wrong trailer", good.replace(TRAILER + TAIL, b"\x86\x85" + TAIL)),
    ]
    for name, blob in negatives:
        got = decode(blob)
        ok = got is None
        lines.append(f"fixture {name:<16} {'ok  ' if ok else 'FAIL'} "
                     f"expected None, got {'None' if got is None else 'a string'}")
        all_ok &= ok
    return lines, all_ok


def _structure(blob, text_len):
    """Structural verdict for a mismatch line. Reads lengths, never content."""
    start = blob.find(MARKER)
    i = start + len(MARKER) + 5
    if start < 0 or i >= len(blob):
        return "unparsed", "no"
    tag = blob[i]
    if tag <= 0x7F:
        length, i = tag, i + 1
    elif tag == 0x81:
        length, i = int.from_bytes(blob[i + 1:i + 3], "little"), i + 3
    elif tag == 0x82:
        length, i = int.from_bytes(blob[i + 1:i + 5], "little"), i + 5
    else:
        return "unparsed", "no"
    trailer = blob[i + length:i + length + 2] == TRAILER
    return ("equal" if length == text_len else "not"), ("yes" if trailer else "no")


def _year(raw):
    """message.date: nanoseconds since 2001-01-01; older rows hold seconds."""
    seconds = raw / 1e9 if abs(raw) > 1e11 else raw
    return datetime.fromtimestamp(seconds + APPLE_EPOCH, timezone.utc).year


def ground_truth(path, since_days):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.text_factory = bytes          # compare raw bytes; never decode text here
    where = "text IS NOT NULL AND attributedBody IS NOT NULL"
    if since_days is not None:
        where += (f" AND date/1000000000 + {APPLE_EPOCH} >= "
                  f"CAST(strftime('%s', 'now', '-{int(since_days)} days') AS INTEGER)")
    rows = conn.execute(
        f"SELECT text, attributedBody, associated_message_type, date "
        f"FROM message WHERE {where}")
    matched = mismatched = overlap = 0
    skips = {"tapback": 0, "attachment-only": 0, "empty": 0}
    details = []
    total = 0
    for text, blob, assoc, raw_date in rows:
        total += 1
        tapback = assoc != 0
        attachment = text == ATTACHMENT
        overlap += tapback and attachment
        if tapback:
            skips["tapback"] += 1
            continue
        if attachment:
            skips["attachment-only"] += 1
            continue
        if text == b"":
            skips["empty"] += 1
            continue
        got = decode(blob)
        if got is not None and got.encode("utf-8") == text:
            matched += 1
            continue
        mismatched += 1
        length, trailer = _structure(blob, len(text))
        details.append(f"  mismatch: length={length} trailer={trailer} "
                       f"year={_year(raw_date)} decode={'None' if got is None else 'differs'}")
    conn.close()
    skipped = sum(skips.values())
    scope = f"--since-days {since_days} (gate)" if since_days is not None \
        else "all dual-form rows (informational)"
    print(f"scope: {scope}")
    print(f"dual-form rows: {total}")
    print(f"matched: {matched}")
    print(f"mismatched: {mismatched}")
    print(f"skipped: {skipped}  = " + " + ".join(f"{k} {v}" for k, v in skips.items()))
    print(f"  overlap tapback & U+FFFC: {overlap} (counted once, as tapback)")
    print(f"  partition check: matched + mismatched + skipped = "
          f"{matched + mismatched + skipped} of {total}")
    for line in details:
        print(line)
    return mismatched == 0 and matched + mismatched + skipped == total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ground-truth", metavar="PATH")
    parser.add_argument("--since-days", type=int)
    args = parser.parse_args()
    provenance()
    if args.ground_truth:
        ok = ground_truth(args.ground_truth, args.since_days)
    else:
        lines, ok = fixtures()
        for line in lines:
            print(line)
        print(f"\n{'ALL PASS' if ok else 'NOT ALL PASS'}")
    sys.exit(0 if ok else 1)


main()
