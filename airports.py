"""IATA airport code -> IANA time zone, for endpoints named only by a code.

Ruled 2026-09-26 (second session), fix shape (B) for r24: an endpoint the
records name only by an airport code takes its render zone from this table,
not from the model. This amends the 2026-09-24 ruling (Option D) that every
render zone is decided on the synthesis side: synthesis still decides every
endpoint except codes, and codes are a table because they are a table. The
09-11 r24 record (gmail, "TPA to DFW", no location) drew New York or null
from the same request - null 10 of 16 short replies on 2026-09-26 - and a
defect whose signature is nondeterminism is closed by determinism.

Self-authored, no dependency, no wholesale import. Two sources only:
  - every code the corpus or the fixtures actually carry (TPA, ATL, SEA,
    DFW from the 22 recorded mornings; DEN and LAX from the fixtures), and
  - the obvious US hubs, so the next trip's codes-only record does not hit
    the tripwire on its first morning. The hub list is from knowledge, not
    checked against the FAA's published large-hub list.
Every entry was checked by hand: the zone is the one the airport's own city
keeps, including the two that are not a region's default - Phoenix does not
observe DST (America/Phoenix, not America/Denver), and Detroit has its own
key (America/Detroit).

A code not here is not guessed. lookup() returns None, the caller logs
SYNTHESIS ZONE naming the code, and the model's value stands: the table can
be incomplete without being silent. Adding a code is adding a line here,
hand-checked, never a rule that derives one.

Display-only, like every render zone: nothing here reaches _distinct_times,
derive_conflicts, identity, state, or the Reminders path.
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

AIRPORT_ZONES = {
    # Corpus and fixtures - the codes with a record behind them.
    "ATL": "America/New_York",      # Atlanta (corpus 09-04..09-12, fixture b)
    "DEN": "America/Denver",        # Denver (fixture b)
    "DFW": "America/Chicago",       # Dallas/Fort Worth (corpus, r24, fixtures a, f1, f2)
    "LAX": "America/Los_Angeles",   # Los Angeles (fixture f2)
    "SEA": "America/Los_Angeles",   # Seattle-Tacoma (corpus 09-04..09-12)
    "TPA": "America/New_York",      # Tampa (corpus, r24, fixtures a, f1)
    # US hubs - no record behind them yet.
    "AUS": "America/Chicago",       # Austin
    "BNA": "America/Chicago",       # Nashville
    "BOS": "America/New_York",      # Boston
    "BWI": "America/New_York",      # Baltimore/Washington
    "CLT": "America/New_York",      # Charlotte
    "DAL": "America/Chicago",       # Dallas Love Field
    "DCA": "America/New_York",      # Washington National
    "DTW": "America/Detroit",       # Detroit
    "EWR": "America/New_York",      # Newark
    "FLL": "America/New_York",      # Fort Lauderdale
    "HNL": "Pacific/Honolulu",      # Honolulu
    "IAD": "America/New_York",      # Washington Dulles
    "IAH": "America/Chicago",       # Houston Intercontinental
    "JFK": "America/New_York",      # New York JFK
    "LAS": "America/Los_Angeles",   # Las Vegas
    "LGA": "America/New_York",      # New York LaGuardia
    "MCO": "America/New_York",      # Orlando
    "MDW": "America/Chicago",       # Chicago Midway
    "MIA": "America/New_York",      # Miami
    "MSP": "America/Chicago",       # Minneapolis-St. Paul
    "ORD": "America/Chicago",       # Chicago O'Hare
    "PHL": "America/New_York",      # Philadelphia
    "PHX": "America/Phoenix",       # Phoenix (no DST)
    "SAN": "America/Los_Angeles",   # San Diego
    "SFO": "America/Los_Angeles",   # San Francisco
    "SLC": "America/Denver",        # Salt Lake City
}


def lookup(code):
    """The IANA zone for an IATA code, or None when the table lacks it.
    Exact key match: no case folding, no trimming - the discriminator in
    synthesize.py only ever hands over three A-Z characters."""
    return AIRPORT_ZONES.get(code)


def self_check():
    """Codes whose zone ZoneInfo cannot resolve; empty when the table is good.

    Ruled 2026-09-27: runs as part of the fixture pass and fails it on any
    entry. The override hands table values to validate(), which would null an
    unresolvable one and log it - correct at run time, but a typo here should
    fail a build, not surface one morning at a time.
    """
    bad = []
    for code, zone in AIRPORT_ZONES.items():
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            bad.append(code)
    return bad
