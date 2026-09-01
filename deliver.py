import time
from datetime import datetime

from digest import run_digest
from send_push import send_push

BUDGET = 950  # bytes, measured as len(s.encode("utf-8"))


def plan_parts(full_body, budget):
    """Pure. Return the list of message strings to send: full_body fits ->
    [full_body]; otherwise split full_body into parts each under budget,
    preferring the last blank-line boundary under budget and falling back to
    any newline boundary. Split parts are prefixed with "(i/n)  " and that
    prefix counts toward the part's budget.

    Splitting is always preferred to dropping content — two messages beat one
    message with a section deleted."""
    def blen(s):
        return len(s.encode("utf-8"))

    if blen(full_body) <= budget:
        return [full_body]

    def byte_prefix_len(s, cap):
        total = 0
        for i, ch in enumerate(s):
            b = len(ch.encode("utf-8"))
            if total + b > cap:
                return i
            total += b
        return len(s)

    def split(text, cap):
        chunks = []
        rest = text
        while rest:
            if blen(rest) <= cap:
                chunks.append(rest)
                break
            cut = byte_prefix_len(rest, cap)
            window = rest[:cut]
            idx = window.rfind("\n\n")          # prefer last blank-line boundary
            if idx > 0:
                part, nxt = rest[:idx], idx + 2
            else:
                idx = window.rfind("\n")         # fall back to any newline
                if idx > 0:
                    part, nxt = rest[:idx], idx + 1
                else:
                    part, nxt = window, cut       # hard cut to guarantee progress
            chunks.append(part)
            rest = rest[nxt:]
        return chunks

    if budget <= len("(1/1)  "):
        raise ValueError(f"budget {budget} too small to split")

    # Reserve prefix width; iterate because n's digit-count feeds back into the
    # per-part budget. total <= n guarantees actual prefixes fit the reserve.
    n = 1
    while True:
        reserve = len(f"({n}/{n})  ")
        chunks = split(full_body, budget - reserve)
        if len(chunks) <= n:
            break
        n = len(chunks)
    total = len(chunks)
    return [f"({i}/{total})  {c}" for i, c in enumerate(chunks, 1)]


if __name__ == "__main__":
    print(f"RUN START {datetime.now().isoformat(timespec='seconds')}")
    title, full_body = run_digest()
    parts = plan_parts(full_body, BUDGET)
    print(f"PRE-SEND {datetime.now().isoformat(timespec='seconds')}")
    for i, part in enumerate(reversed(parts)):
        if i:
            time.sleep(2)
        send_push(part, title)
    print(f"Sent via Pushover ({len(parts)} part(s)).")
