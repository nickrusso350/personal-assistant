"""Spoken destinations for the weekly video (ruled 2026-11-10; built
2026-11-11). Video side only: not on the 07:00 path, imported by wavetops.py
and nothing the scheduled run executes. airports.py is the digest's IATA ->
IANA zone table and is separate by ruling; this module never reads it.

Two lookups, both exact, no scoring (the fuzzy-matching ban, applied by
shape):

- spoken_destination(code): the city to say for an IATA airport code.
  destinations_overrides.json first, then destinations_airports.csv (a fixed
  OurAirports snapshot, public domain, provenance in its header). A code in
  neither - or a snapshot row whose municipality is empty - is None and logs
  one DESTINATION UNKNOWN line; the sentence then leaves the destination out
  (ruled 2026-11-10).
- city_from_location(s): the City token of a location string whose tail is
  ", City, ST" with ST two A-Z characters, optionally followed by a ZIP
  (" NNNNN", " NNNNN-NNNN", or ", NNNNN") and then optionally by
  ", United States" or ", USA" (ruled 2026-11-11, widened the same day, for
  the no-flight trip sentence). The token is returned verbatim. Anything
  else is None. Known wart, accepted: the hotel's city, not the trip's.

Logging follows the 2026-09-21 rule that a library function never prints
unconditionally: spoken_destination takes log=True and a quiet caller passes
log=False. city_from_location never prints; its caller logs the drop.

Both data files load once, on first use, from beside this module. No
network, no model call.
"""
import csv
import json
import re
from functools import cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / "destinations_airports.csv"
OVERRIDES = HERE / "destinations_overrides.json"

# ", City, ST", then optionally a ZIP (" NNNNN", " NNNNN-NNNN", or
# ", NNNNN"), then optionally ", United States" or ", USA", anchored at the
# end (widened 2026-11-11 - the first ruled form ended at ST and was read
# off digit-masked output; it matched 0 of 63 lodging records). City holds
# no comma and is returned verbatim; ST is exactly two A-Z. Literal
# alternatives only, no scoring.
_TAIL = re.compile(
    r", ([^,]+), ([A-Z]{2})"
    r"(?: [0-9]{5}| [0-9]{5}-[0-9]{4}|, [0-9]{5})?"
    r"(?:, United States|, USA)?\Z"
)


@cache
def _snapshot():
    """IATA code -> municipality (may be "") from the snapshot."""
    with SNAPSHOT.open(newline="", encoding="utf-8") as fh:
        lines = (line for line in fh if not line.startswith("#"))
        return {r["iata_code"]: r["municipality"] for r in csv.DictReader(lines)}


@cache
def _overrides():
    return json.loads(OVERRIDES.read_text(encoding="utf-8"))["overrides"]


def spoken_destination(code, log=True):
    """The city to speak for an IATA code: override, then snapshot, else
    None with one DESTINATION UNKNOWN <code> line (silenced by log=False).
    The code is matched byte-exact; it is never upper-cased or trimmed."""
    override = _overrides().get(code)
    if override is not None:
        return override
    city = _snapshot().get(code)
    if city:
        return city
    if log:
        reason = "empty municipality" if city == "" else "not in snapshot or overrides"
        print(f"DESTINATION UNKNOWN {code}: {reason}")
    return None


def city_from_location(s):
    """The City token of a ", City, ST[ ZIP][, United States|, USA]" tail,
    verbatim; else None.

    "..., Orlando, FL" -> "Orlando"; "..., Fort Worth, TX 76116, United
    States" -> "Fort Worth"; "..., East Point, GA, 30344" -> "East Point".
    A non-US tail, a lower-case state, or any other trailer is None.
    """
    if not isinstance(s, str):
        return None
    m = _TAIL.search(s)
    return m.group(1) if m else None
