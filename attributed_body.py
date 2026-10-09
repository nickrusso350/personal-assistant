"""Decode the message body from a Messages `message.attributedBody` blob.

Written 2026-10-09 for the iMessage-as-source build. Ruled 2026-10-08: bodies
are decoded from `attributedBody` (typedstream) by a minimal hand-written
parser in the repo - no third-party dependency, no Swift/ObjC shell-out.
Stdlib only. NOT on the 07:00 path until the fetch stage is wired by ruling.

Why: most bodies are not in `message.text`. On 2026-10-07, 1,015 of 1,187
messages in the prior 30 days had `text` NULL; the body lives only here.

Layout (measured 2026-10-09 against a read-only copy of chat.db):

    b"NSString"  01 ?? 84 01 2b  <length>  <string bytes, UTF-8>  86 84

  Introducer: 0x01, any single byte, then 84 01 2b. The second byte is
    not pinned (ruled 2026-10-09). Two values are observed, and they track
    the marker's offset:
        0x94 with NSString at offset 60  -  52 of 171 rows in the 30-day
            dual-form set, 48,640 of 152,030 over all history;
        0x95 with NSString at offset 108 - 119 of 171, 103,390 of 152,030.
    It reads as a typedstream bookkeeping index (a class reference, whose
    number depends on what was archived before it), not as part of the
    string's shape. The trailer check below is the correctness guard.

  Length: a typedstream integer, in one of three forms (ruled 2026-10-09):
        0x00-0x7F            the length itself, one byte;
        0x81 + uint16 LE     from 128 (every 128- and 129-byte text
                             observed uses this form);
        0x82 + uint32 LE     encoder-only: kept for completeness, never
                             observed in real rows (no text reaches 65,536
                             bytes).
    Any other first byte, 0x80 included, is not a length: None.
    The length counts UTF-8 bytes, not characters.

  Trailer cross-check: the two bytes immediately after the string must be
    86 84. A length that lands anywhere else is a misparse, and the blob
    yields None rather than a string of the wrong extent.

Never partial (ruled 2026-10-08): decode() returns the whole body or None.
Any failure - marker absent, introducer wrong, unknown length form, blob
truncated, invalid UTF-8 (errors="strict"), trailer absent, any exception -
returns None. decode() never logs; the caller logs the guid and skips the
message, and the morning delivers.
"""

MARKER = b"NSString"
TRAILER = b"\x86\x84"


def decode(blob):
    """Return the message body from an attributedBody blob, or None.
    Never a partial string; never logs."""
    try:
        start = blob.find(MARKER)
        if start < 0:
            return None
        i = start + len(MARKER)
        intro = blob[i:i + 5]
        if len(intro) != 5 or intro[0] != 0x01 or intro[2:] != b"\x84\x01\x2b":
            return None
        i += 5
        tag = blob[i]
        if tag <= 0x7F:
            length, i = tag, i + 1
        elif tag == 0x81:
            field = blob[i + 1:i + 3]
            if len(field) != 2:
                return None
            length, i = int.from_bytes(field, "little"), i + 3
        elif tag == 0x82:
            field = blob[i + 1:i + 5]
            if len(field) != 4:
                return None
            length, i = int.from_bytes(field, "little"), i + 5
        else:
            return None
        body = blob[i:i + length]
        if len(body) != length:
            return None
        if blob[i + length:i + length + 2] != TRAILER:
            return None
        return body.decode("utf-8", errors="strict")
    except Exception:                             # never partial, never raise
        return None
