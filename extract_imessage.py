"""Extract commitments from one text message. Sibling of extract.py.

Written 2026-10-05 for the iMessage-as-source design table (ruled 2026-10-04).
NOT on the 07:00 path: nothing the scheduled run imports imports this module,
and it stays that way until the fetch stage is built and wired by ruling.

What is shared with extract.py, deliberately:
  - The output schema, byte-identical: a list of
    {"type", "what", "date", "time", "action_needed"}. State, synthesis, the
    renderer and Reminders consume it; it must not fork (ruled 2026-10-04).
  - The call and the parse guard: load_env_file(), anthropic.Anthropic(),
    claude-sonnet-4-6, max_tokens 600, code-fence strip, JSON parse, bare
    object wrapped in a list.
  - The field rules, verbatim, except the clock-hour convention appended to
    the "time" rule (ruled 2026-10-09).

What is the texts' own:
  - The anchor is the message's send time and weekday, never date.today().
    Relative dates resolve at extraction against it (ruled 2026-10-04):
    "this X" and a bare day-word mean the nearest upcoming X, the send day
    included; "next X" means the one after.
  - The conversation window is context only. An obligation attaches to the
    message that completes it; the window is fenced off in the prompt and
    nothing is extracted from it.
  - Both directions are read. "me" is the account owner.
  - A text never yields reply_needed.

The mail prompt (extract.py) is not edited (ruled 2026-10-04).

Raw handles never appear in the prompt (ruled 2026-10-05): the owner is
"me", other parties are labeled in order of first appearance within the
window (see _labels).

apply_window_guard is the post-extraction guard: a resolved date outside
[send day, send day + GUARD_DAYS] is dropped with an IMESSAGE GUARD line,
never rendered. So is a null or unparseable date: a text commitment with no
fixed date never renders (ruled 2026-10-05).

apply_what_gate is the post-extraction title gate (ruled 2026-10-09): a
"what" that fails what_clean has every banned token stripped, with the
joiner word before it, and logs one WHAT SANITIZED line; a title left empty
drops its item. Order is gate, then guard, in the harness and in production.
"""
import anthropic
import json
import re
import string
from datetime import date, datetime

from env_loader import load_env_file

# Placeholder (ruled 2026-10-05); re-set from measurement against real threads.
GUARD_DAYS = 120

PROMPT = """You are a precise data-extraction tool. Extract all commitments from the TARGET text message below and return them as a JSON array of objects.
The TARGET message was sent {anchor}. Treat that send date as "today" everywhere below, and use it as the anchor for any relative date reasoning.
Return ONLY the JSON array. No preamble, no explanation, no markdown code fences. Your entire response must be valid, parseable JSON and nothing else.

These are text messages between the account owner and people they know. The sender "me" is the account owner; every other sender is someone else. A commitment can come from either direction: something the owner agreed to, proposed and had accepted, or stated they will do.

A commitment is extracted only when ALL of these hold:
  - it has a fixed when — a date, a day-word, or a date and time;
  - the account owner is a party to it;
  - THIS TARGET message completes it. A proposal or question that still waits on an answer extracts nothing; the message that accepts or settles it is the one that extracts, together with the details agreed earlier in the conversation.

A statement by me of what I will do by a day ("I'll bring the forms Tuesday") is a complete commitment on its own — a task — and needs no reply.

A reply that affirms a proposal completes the arrangement, however it is worded — "yes", "sure", "ok", "that works" are illustrations, not a list. An affirmation needs no time of its own; if the proposal carried a date, the affirming reply completes it.

Most text messages contain NO commitment. Return an empty array [] for:
  - chatter, small talk, reactions, acknowledgments with nothing to settle;
  - open questions and proposals not yet answered;
  - open-ended intentions with no fixed when — "sometime", "we should", "let's do that soon";
  - notifications, verification codes, delivery updates, promotions and marketing texts.
If the commitment has no date that can be resolved, return [].

Never return "reply_needed" for a text message. A question waiting on the owner is not a commitment; return [] for it.

Day-words resolve against the send date by this convention:
  - "this <day>" and a bare day-word ("Thursday") mean the nearest upcoming <day>, counting the send day itself — a message sent on a Thursday that says "Thursday" means that same day;
  - "next <day>" means the one after that.
Bare numbers in a scheduling context are times ("2 works" answering a meeting question is a time, not a quantity).

A message may contain MORE THAN ONE commitment. Return one object per commitment.

Each object in the array uses exactly this structure:
{{"type": "...", "what": "...", "date": "...", "time": "...", "action_needed": "..."}}

Field rules:
- "type": one of "appointment", "task", or "reply_needed".

    OWNERSHIP TEST — a commitment must be an obligation YOU personally own.
    Some messages use task-shaped or reply-shaped language but the obligation
    actually sits with the SENDER, not you. Do NOT create an object for these —
    omit them from the array entirely:
      - A company soliciting a favor — "review your purchase", "rate your
        order", "take our survey", "leave feedback" — is not a task.
        You never committed to it.
      - A courtesy acknowledgment where the other party holds the next move —
        "thanks, we'll take a look and get back to you", "keep us posted if
        anything changes", "we'll be in touch" — is not reply_needed.
        They owe the next step, not you.

    Only include a commitment when the message explicitly states it AND the
    recipient owns it:
    "appointment" = a scheduled event at a date/time.
    "task" = something YOU need to do by a date — that you committed to or are
        responsible for, not a favor a company is requesting.
    "reply_needed" = someone is specifically waiting on and requesting a
        response FROM YOU now (for example, "please reply to confirm") — not a
        standing courtesy where the sender will follow up.
    Do not infer or invent a commitment that the message does not explicitly
    state. When in doubt, omit it from the array.
- "what": a short description of the commitment. Name the activity only; never restate the date or time in it.
- "date": the relevant date in YYYY-MM-DD format. If the message gives a date with no year, assume the next future occurrence of that date relative to today. If no date is present, use null.
- "time": the time of day in 24-hour HH:MM format. If no time is present, use null. A bare hour with no am/pm reads as PM for 1–7, AM for 8–11, and noon for 12, unless the activity makes the other reading obvious ("breakfast at 7" is 07:00, "dinner at 8" is 20:00). When the hour cannot be resolved, "time" is null rather than a guess.
- "action_needed": a concrete action the recipient is explicitly required to take. Default to null. Only fill this in if the message directly instructs the recipient to DO something. Do not infer, invent, or imply an action that the message does not explicitly state. The following are NOT actions: a phone number offered in case of questions, contact information, opt-out instructions, or any purely informational detail. Attending a scheduled appointment is captured by "type" and "what" — it is not an action_needed. If the message only informs, use null.

For text messages, "you" and "the recipient" in the field rules mean the account owner ("me"), whichever direction the TARGET message was sent. The text-message rules above take precedence over the field rules: in particular, never return "reply_needed".

Earlier messages in this conversation, oldest first. FOR UNDERSTANDING ONLY — extract NOTHING from these; extract ONLY from the TARGET message:
<<<CONTEXT
{context}
CONTEXT>>>

TARGET message (sent {anchor}, sender: {sender}):
\"\"\"
{message}
\"\"\""""


def _anchor(ts):
    """'2026-10-05 10:05 (Monday)' — send time plus weekday."""
    return f"{ts:%Y-%m-%d %H:%M} ({ts:%A})"


def _labels(senders):
    """Map each handle to its prompt label (ruled 2026-10-05). "me" stays
    "me"; other parties are numbered in first-appearance order: "them",
    "them-2", "them-3"... A one-to-one chat therefore reads "them" throughout,
    and no raw handle reaches the prompt."""
    labels = {"me": "me"}
    n = 0
    for s in senders:
        if s not in labels:
            n += 1
            labels[s] = "them" if n == 1 else f"them-{n}"
    return labels


def build_prompt(message, sender, anchor_ts, context):
    """Render PROMPT for one TARGET message.
    anchor_ts is the message's send time (datetime). context is the list of
    earlier messages in the window, oldest first, each a dict with "sender",
    "ts" (ISO string or datetime) and "text". Senders are relabeled by
    _labels over the window plus the target."""
    labels = _labels([m["sender"] for m in context] + [sender])
    lines = []
    for m in context:
        ts = m["ts"] if isinstance(m["ts"], datetime) else datetime.fromisoformat(m["ts"])
        lines.append(f"[{_anchor(ts)}] {labels[m['sender']]}: {m['text']}")
    return PROMPT.format(
        anchor=_anchor(anchor_ts),
        sender=labels[sender],
        message=message,
        context="\n".join(lines) if lines else "(none)",
    )


def extract_text_commitments(message, sender, anchor_ts, context):
    """Take one text message, return a LIST of commitment dicts in extract.py's
    schema. Returns [] when the message completes no commitment."""
    prompt = build_prompt(message, sender, anchor_ts, context)
    load_env_file()
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        messages=[
            {"role": "user", "content": prompt}
        ],
    )

    # ─── Parse guard (as extract.py) ───────────────────────────
    raw_output = response.content[0].text.strip()
    # Strip a markdown code fence if the model added one despite instructions.
    if raw_output.startswith("```"):
        raw_output = raw_output.split("\n", 1)[-1]   # drop the opening ``` line
        if raw_output.endswith("```"):
            raw_output = raw_output[:-3]
        raw_output = raw_output.strip()
    try:
        parsed = json.loads(raw_output)
    except json.JSONDecodeError:
        raise ValueError("Model did not return valid JSON: " + repr(raw_output))
    # Safety net: if the model returns a bare object instead of an array,
    # wrap it so callers always receive a list (prevents silent mis-iteration).
    if isinstance(parsed, dict):
        parsed = [parsed]
    return parsed


WHAT_BANNED = frozenset(
    "monday tuesday wednesday thursday friday saturday sunday "
    "mon tue tues wed thu thur thurs fri sat sun "
    "january february march april may june july august september october november december "
    "jan feb mar apr jun jul aug sep sept oct nov dec am pm".split()
)

# A banned token's joiner: stripped with it when it is the word immediately before.
WHAT_JOINERS = frozenset("on at in this next for".split())


def what_clean(what):
    """Ruled 2026-10-08: "what" names the activity only. Fails on any digit or any
    whole-word weekday name, month name, or am/pm token (case-insensitive)."""
    if any(ch.isdigit() for ch in what):
        return False
    tokens = re.findall(r"[a-z]+", what.lower())
    return not any(t in WHAT_BANNED for t in tokens)


def _banned_word(word):
    """A whitespace-delimited word is banned whole when it holds a digit (3pm,
    15:00 and 3rd go entire, ruled 2026-10-09) or any banned letter run."""
    return not what_clean(word)


def apply_what_gate(parsed):
    """Sanitize any "what" that fails what_clean (ruled 2026-10-09). Strip every
    banned word and the joiner immediately before it, collapse whitespace, trim
    leading/trailing punctuation, capitalize the first character. Each change
    prints one WHAT SANITIZED line; an item whose title is left empty is
    dropped with a WHAT SANITIZED dropped line. Returns the kept list.
    Accepted cost (ruled 2026-10-09): "Lunch at 3 Oaks" becomes "Lunch Oaks"."""
    kept = []
    for item in parsed:
        # Non-dict items, and dicts whose "what" is not a string, pass unchanged.
        if not isinstance(item, dict) or not isinstance(item.get("what"), str):
            kept.append(item)
            continue
        before = item["what"]
        if what_clean(before):
            kept.append(item)
            continue
        words = before.split()
        drop = [False] * len(words)
        for i, word in enumerate(words):
            if _banned_word(word):
                drop[i] = True
                if i > 0 and words[i - 1].lower().strip(string.punctuation) in WHAT_JOINERS:
                    drop[i - 1] = True
        after = " ".join(w for w, d in zip(words, drop) if not d)
        after = after.strip(string.punctuation + string.whitespace)
        if not after:
            print(f"WHAT SANITIZED dropped before={before!r}")
            continue
        after = after[0].upper() + after[1:]
        print(f"WHAT SANITIZED before={before!r} after={after!r}")
        kept.append({**item, "what": after})
    return kept


def apply_window_guard(parsed, anchor_ts, days=GUARD_DAYS):
    """Drop any commitment whose date falls outside [send day, send day + days].
    Each drop prints one IMESSAGE GUARD line; the kept list is returned.
    A null date is dropped: a text commitment with no fixed date never
    renders. An unparseable date is dropped: it cannot be placed in the
    window. (Both ruled 2026-10-05.)"""
    anchor_day = anchor_ts.date()
    kept = []
    for item in parsed:
        raw = item.get("date")
        if raw is None:
            print(f"IMESSAGE GUARD dropped date=null anchor={anchor_day} "
                  f"what={item.get('what')!r}")
            continue
        try:
            delta = (date.fromisoformat(raw) - anchor_day).days
        except (TypeError, ValueError):
            print(f"IMESSAGE GUARD dropped date={raw!r} anchor={anchor_day} "
                  f"delta=unparseable what={item.get('what')!r}")
            continue
        if delta < 0:
            print(f"IMESSAGE GUARD dropped date={raw} anchor={anchor_day} "
                  f"delta={delta}d < 0d what={item.get('what')!r}")
            continue
        if delta > days:
            print(f"IMESSAGE GUARD dropped date={raw} anchor={anchor_day} "
                  f"delta={delta}d > {days}d what={item.get('what')!r}")
            continue
        kept.append(item)
    return kept
