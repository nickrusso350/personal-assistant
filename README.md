# Personal Assistant

**A daily-digest system that reports obligations which already exist — and never generates its own.**

Every morning at 7:00, it reads Gmail and Google Calendar, extracts commitments with the Claude API, reconciles them against what's already been resolved, and delivers a three-section digest as a Pushover notification:

- **NEEDS ATTENTION** — dated tasks now overdue
- **TO DO** — dateless tasks (shown daily until resolved) and dated tasks within 7 days
- **COMING UP** — calendar events and extracted appointments, merged chronologically, 7-day horizon

Open action items are also written to a dedicated Apple Reminders list. Ticking an item on the phone resolves it — the next morning's run reads the completion and the item vanishes from the digest. No terminal, no laptop, no app beyond what's already on the phone.

It runs unattended in production via launchd on macOS. Typical run: fires at 7:00 (launchd's start delay varies by host — seconds to tens of minutes observed) and reaches the send 16–109 seconds after it starts (17 runs measured, 2026-08-30 to 2026-09-14).

<!-- screenshot slot: scrubbed Pushover digest goes here -->

*Known limitations are documented [below](#known-limitations) — including the ones found on real mornings.*

---

## Design decisions

### The product had to be discovered, not specified

The original design generated proposals: it drafted calendar events from extracted commitments and asked for approval each morning. Every layer worked. The output was unusable — the user had become the assistant's clerk, approving work the assistant had invented. One screenshot ended it: a Delta flight the assistant proposed writing was already in the calendar, written by Google's travel parser, with an end time (`7:40 PM`) that existed nowhere in the assistant's pipeline — Google had parsed the full flight range while the assistant defaulted to start + 1 hour. For the highest-volume mail category, the system was paying to extract, proposing a duplicate, and demanding approval for something already handled better.

The write path was mothballed the same week, and the product statement was rewritten to what the system actually is: **it reports obligations that already exist; it never generates its own.** A corollary that shapes every rendered line: no imperatives in the deliverable. The digest states what exists; it never instructs.

### Read-only, with one sanctioned exception

The assistant reads Gmail and Calendar and writes to neither — the OAuth grants are read-only scopes, so the boundary is enforced by the token, not just by discipline. The single write surface is a dedicated Apple Reminders list — and *dedicated* is load-bearing. Writing an extracted commitment there is rendering an existing obligation to a second surface, not creating one; and a contained namespace's worst failure is a junk list you delete, unlike schedule data the assistant doesn't own.

### Identity is exact, and fuzzy matching is banned

A commitment's identity is `(gmail message id, array position)`. Resolution happens only when `completed = true` appears on the exact stored reminder id — titles are display-only, deletion means nothing, and no similarity judgment ever resolves, deletes, or merges a record. The reasoning: over-matching silently deletes real commitments; under-matching makes noise. Noise is survivable; silent data loss is not. The ban was stress-tested when a title collision put a completion on the wrong item — id-based resolution absorbed it.

The ban has been widened exactly once, deliberately, and only into reminder minting (2026-09-03). When the synthesis stage (below) groups two commitments as one obligation, the second reuses the first's reminder instead of minting a duplicate. The guardrails are structural: code validates the grouping as a strict partition before anything consumes it, every grouping is logged, and members are add-only — an existing reminder is never rewritten or moved. Sharing writes a reminder id, never a commitment id, so identity is untouched; and when synthesis fails, write-back degrades to one reminder per commitment, never to silence.

### Completeness beats brevity

The digest never suppresses an obligation because another system also tracks it. When Google's travel parser and the assistant's extraction both saw one hotel booking, both records survived; only the display collapsed them, into one line built from one record's real fields. Where the sources disagree on the time, the line says so instead of picking a winner. Deduplication collapses the display; it never drops data.

Completeness decides *whether* an obligation appears. It does not decide how finely an obligation is sliced — that is granularity, a separate axis with its own ruling, below. Folding a connecting flight's legs into one journey line is not a retreat from completeness: no record is discarded, the obligation still appears, and the folding happens only at render.

### Granularity is its own axis

Ruled 2026-09-13, in the owner's words: *"The purpose of the digest isn't to map out every second of my day, but to give me the information I need for the day and the next seven days, based on all of my extracted sources."*

The working test is one question: **does this line change what the user would do at 7 AM?** A connection doesn't; the journey does. So a trip booked under one confirmation code is one obligation whatever its flight numbers, airports, or times — rendered as its two endpoints, a Departure line and an Arrival line, never one line per leg. A same-day out-and-back under one booking is still one journey, so it reads as one departure and one arrival rather than four legs — a cost accepted deliberately at the ruling, not discovered later.

Granularity is easy to confuse with two other rules, and it is neither. The source rule asks whether an obligation exists in a system the assistant reads. Deduplication asks whether two records are the same obligation. Granularity asks what grain earns a line. Each has its own ruling, and none can be satisfied by bending another. The principle is written into the synthesis prompt above the rules, so the model reads the purpose before the keys.

### The identity contract lives in the prompt

Since 2026-09-04, a synthesis stage sits between extraction and rendering. One Claude API call per morning receives the day's records and returns structure plus one label: which records are one obligation, which member it proposes as primary (code selects; see Primary selection), and an optional trip label with its phase (depart or return). Every rendered line is built from the members' real fields, and which member supplies which line is the renderer's rule rather than the model's: a journey's Arrival comes from the member whose end instant is latest (2026-09-16), which need not be the member the model marked primary. The model never emits a time, a code, or a flight number, so a hallucinated fact is impossible by construction; the trip label is the only model-authored text that reaches the page. The prompt forbids inventing it — the destination must be a city named in the records — but code checks only that it is a non-empty string, so a wrong label would render as written. Code validates the grouping — a strict partition, one date per group, no task grouped with an event — and any failure renders a deterministic fallback with a note; synthesis never costs the morning. The rules the model groups by are the identity contract, rules 1–10 in `synthesize._prompt`.

The first contract failed in a way that looked like bad model judgment. Three of its rules contradicted each other: a shared flight number was sufficient to merge (rule 2a); an itinerary merged with its first leg only when they shared a departure time (rule 5); and two legs of one journey were two groups (rule 3). Handed a real connecting itinerary, the model complied with all three at once, and the output was an arbitration — which clause won depended on what else was on that morning's list. The same journey regrouped differently on 9/11 and 9/12, at temperature 0. Not sampling, not capability: a specification defect, diagnosed 2026-09-13. When output is an arbitration, look for the contradiction before concluding the model judges badly.

The rewrite (`7228e20`) replaces competing heuristics with decisive keys. Records on one date that share a confirmation code, reservation number, or flight number are one obligation (rules 2–3); a different date always splits. The uncertainty brake survives, scoped: "when uncertain, do not merge" no longer applies once a decisive key is met (rule 8). The model proposes a primary (rule 9); code selects it (below).

**Primary selection (ruled 2026-10-02, built 2026-10-03).** The model proposes, code selects by an exact ordered rule. After `validate()` returns, `synthesize._select_primaries` re-picks the primary of every group with two or more members; the first term that separates two members wins, with byte comparisons throughout:

0. journey groups only (two or more distinct flight numbers, decided by code): earliest start instant, compared in UTC; a member with no instant — a bare gmail clock, an all-day record — sorts after every member with one
1. a stored start zone over none
2. a timed start over an all-day record
3. a calendar record over a gmail one
4. a non-empty location over an empty one
5. the shortest summary, by UTF-8 byte length
6. the lowest record id, as a string

When code's pick differs from the model's, the group's primary is rewritten and one `SYNTHESIS PRIMARY` line logs the members, both picks, and the deciding term. The reason it moved: the model's primary drifted across replays of the same morning at temperature 0, and when two copies of one obligation store different clocks — a calendar copy at 04:00 Pacific, a gmail copy at a bare 07:00 — a flip onto the gmail copy put a wrong time on the page. Conflicts are computed from instants and could not catch it, because which copy *renders* is not a conflict. Display-only: identity, state, conflicts, and Reminders never read the primary.

Rule 5 names what never separates records: time, zone, wording, phrasing, and source. That list is as load-bearing as the keys. A contract that names only the keys leaves everything else to judgment, and a capable model will treat whatever differs between two records — a clock time, a zone label, a turn of phrase — as evidence they are different things. Silence is not neutrality.

Conflicts left the model entirely (2026-09-04). Across live runs at a fixed prompt, membership, primary, and container held steady, while the reported conflicts moved on all three prompt versions tried. It was the one field that asked for a judgment ("do these disagree materially?") rather than a structure — exactly where temperature 0 stops buying determinism. Code now derives conflicts from the members' real values, with time the only field reported. Since 2026-09-14 (`1cc90a4`) it compares instants, not clock strings, so one flight stored as 19:30 Eastern by one writer and 18:30 Central by another no longer reads as a disagreement. Time disagreement is reported; it never separates records. The stability evidence for the rewrite — ten recorded mornings replayed through the new contract — is under [Verification approach](#verification-approach).

---

## Architecture

One linear pass, once per morning:

```
fetch (Gmail + Calendar) → extract (Claude API) → save
    → reconcile (read Reminders completions, resolve by exact id)
    → partition (classify each open commitment — called exactly once)
    → synthesize (Claude API, one call: group records into obligations)
    → write-back (create reminders for new open action items; group members share one)
    → render → split (deliver.py) → deliver (Pushover)
```

Three structural guarantees fall out of the shape. Classification lives in a single function called exactly once per run, and both the render and write-back consume its output — so the digest and the Reminders list cannot disagree, by construction. Reconciliation runs before partition in the same pass, so a ticked item can never render as open. And synthesis runs once, over that same partition, so the grouping the page shows is the grouping write-back uses when group members share a reminder.

Synthesis sits after the partition rather than straight after extraction, and the slot is deliberate: its input is exactly the items the partition will render, plus the calendar's events. Each record carries a per-run id (`r1`, `r2`, …) and a `ref`, the stable key back to the underlying calendar event or commitment, which the renderer and write-back use to find the item again. `ref` is computed before the call and withheld from the model, which sees only the per-run id. It is retained in the logged input, where it is the stable cross-day key `replay_mornings.py` compares groupings on. When synthesis fails — network exhausted after a bounded retry, or a reply that does not validate — the timeline renders ungrouped through the exact-anchor `collapse_display` path under a one-line note, write-back mints one reminder per commitment, and the morning still sends.

Calendar data is display-only: it flows to the synthesis call and the renderer, and never enters state. Extracted appointments auto-drop once their date passes — the date passing *is* the resolution — while dated tasks go overdue and persist. Deletion of a reminder means nothing; only completion on the exact stored id resolves.

The production run has no compact render. A digest too long for one Pushover message is split across several by `deliver.py`, never shortened by collapsing a section — two messages beat one with a section deleted. (`build_digest` keeps a `compact_calendar` flag; only `preview_digest.py` exercises it.)

### File map

| Tier | Files | |
|---|---|---|
| **Repo** | `CLAUDE.md` | the standing design rulings and working rules, written for Claude Code sessions in this repo — the reasoning is the portfolio piece |
| **Pipeline** | `deliver.py` | entry point; launchd runs this. Brackets the run with `RUN START` / `PRE-SEND`, splits an oversized digest into parts (`plan_parts`), sends them |
| | `digest.py` | orchestration, partition, render, and the `collapse_display` fallback |
| | `fetch_gmail.py` / `fetch_calendar.py` | source reads |
| | `extract.py` | Claude API extraction, one call per new message |
| | `synthesize.py` | the synthesis stage: builds records, carries the identity contract (`_prompt`), validates the grouping, derives conflicts in code |
| | `state.py` | persistence, nothing else |
| | `resolve.py` | resolution + manual-resolution fallback picker; `resolve_one` is the single delete site |
| | `reminders_write.py` | the one sanctioned write surface: reconcile, write-back, one shared reminder per grouped obligation |
| | `send_push.py` | Pushover delivery |
| | `watchdog.py` | the second LaunchAgent: at 07:30 reads `digest.out` for today's `RUN START` → `PRE-SEND` → `Sent via Pushover` and pages on absence; weekly heartbeat on Sundays. No Google, no Reminders, no state |
| | `env_loader.py` | credential self-provisioning (launchd inherits almost no environment) |
| **Test / fixture** | `preview_digest.py` | the primary test harness: renders the fixture corpus and replays a recorded synthesis grouping for $0. `--live` is its only networked path — one synthesis call that re-records the grouping |
| | `test_plan_parts.py` | part-splitting cases |
| | `test_reminders_pipeline.py` | reconcile and write-back against fakes; no osascript runs, and the real state file is read before and after to prove it is never written |
| | `test_distinct_times.py` | the four cases behind the instants-not-clock-strings ruling, asserted through `_distinct_times` and both of its consumers |
| | `test_render_zone.py` | eleven rendered lines behind the 2026-09-27 render-zone rulings — convert, label, tiebreak (b) and its negative, the fallbacks, the midnight wart — pinned from a captured run |
| | `fixtures/` | captured duplication cluster + rendered cross-source lines; `grouping_fixture1.json`, the recorded grouping `preview_digest.py` replays; `zone_fixtures.json` (synthesis zones) and `render_zone_fixtures.json` (rendered zones) |
| **Diagnostic** | `synth_dump.py` | prints one recorded morning's synthesis input and result, read-only and $0: identifiers masked to stable tokens (`ID-A`, `FLIGHT-B`) by first appearance, `description_snippet` dropped, emails, phone numbers, and long numbers scrubbed by pattern (names are not). Identity diagnosis needs to know which records share an identifier, never what it says. `end_time` and `end_zone` print unmasked (2026-09-17): a clock and a zone name carry nothing to mask. Opens with a provenance header |
| | `replay_mornings.py` | re-runs recorded mornings through the current contract and compares membership as `ref`-sets; one API call per morning, touches no state. Opens with a provenance header. The first draft of the backlogged `SYNTHESIS DRIFT` tripwire |
| | `probe_calendar.py` | calendar data-surface probe; retained because the timezone work (below) starts with it. Carries its own `SCOPES` and rewrites `token.json`, so it is never run casually |
| **Mothballed** | `calendar_write.py`, `approve.py` | the killed write path — retained unwired; its decision records still feed a rendering rule, and the design reasoning is banked for any future write feature |
| | `send_imessage.py` | retired delivery path |

Mothballed files are kept deliberately: they carry the reasoning that killed them, and deleting them would delete the evidence.

---

## Setup

Documented from the running install, and verified twice: the production host was migrated to a clean machine using this section as the runbook, and on 2026-09-28 a fresh clone on a second machine installed and passed the checks under Verify the install. Eight pieces:

**Prerequisites.** Python 3.14 from python.org — the framework build (see Interpreter and dependencies) — plus git, and the repository itself:

```
git clone https://github.com/nickrusso350/personal-assistant.git
```

On a fresh machine, set `git config user.name` / `user.email` before the first commit.

**Interpreter and dependencies.** Production runs on the python.org framework build (`/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`), deliberately not a package-manager Python. The reason is below in Permissions: macOS Automation grants attach to the *specific interpreter binary*, and a package-manager upgrade replaces that binary — silently invalidating the grant, surfacing only as a missed morning. Dependencies are pinned; install with the production interpreter: `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m pip install -r requirements.txt`. For running the tests without touching the system interpreter, create a venv from the framework build and install the pins there:

```
/Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m venv .venv
.venv/bin/python3 -m pip install -r requirements.txt
```

Production does not use a venv, because the Automation grant attaches to the interpreter binary. Verified 2026-09-28 on a second machine from a fresh clone.

**Credentials.** Three variables in `~/.personal_assistant.env` (chmod 600, never committed): `ANTHROPIC_API_KEY`, `PUSHOVER_USER_KEY`, `PUSHOVER_APP_TOKEN`. Every script self-provisions through `env_loader.py` — necessary because launchd's inherited environment is nearly empty; a script that works from a shell and assumes its environment will silently fail at 7am. Google OAuth needs an app of your own: a Google Cloud project with the Gmail API and the Google Calendar API enabled, and an OAuth client of type Desktop app, whose downloaded JSON is the `client_secret_*.json` below. The scopes requested are the `SCOPES` list in `fetch_gmail.py` — `https://www.googleapis.com/auth/gmail.readonly` and `https://www.googleapis.com/auth/calendar.readonly`. The calendar fetch reuses the Gmail credentials (`fetch_calendar.py` calls `fetch_gmail.get_credentials`), so consent happens once, for both scopes, into one `token.json`. Both files live in the repo directory as `client_secret_*.json` + `token.json`, both gitignored; keep exactly one `client_secret_*.json` there, because `get_credentials` takes the first match. The OAuth app must be published to Production in Google Cloud Console, or refresh tokens expire weekly. One Google Calendar setting is a prerequisite, not a preference: **Show events from Gmail must be off.** Google's parser is a redundant writer — it adds its own copies of bookings the pipeline already reads from the booking mail itself, and it has written wrong instants for flights.

**Schedule.** The LaunchAgent is tracked as a template — `com.nickrusso.dailydigest.plist` at repo root, with `__USER__` standing in for the home-directory component. Install by substitution:

```
sed "s/__USER__/$(whoami)/g" com.nickrusso.dailydigest.plist > ~/Library/LaunchAgents/com.nickrusso.dailydigest.plist
```

The template is *verifiable*: re-run the substitution and `diff` it against the installed file — the tracked config and the config that runs are provably the same bytes, a check a literal copy cannot offer. The agent fires `deliver.py` at 07:00 with stdout/stderr appended to `~/Library/Logs/personal_assistant/`. The watchdog's plist, `com.nickrusso.dailydigest.watchdog.plist`, is the same template and installs by the same substitution; it needs the second Pushover token in the env file and nothing else — no OAuth, no Automation grant. The host does not sleep (`pmset sleep 0`), with a repeating `pmset` wake at 6:55 kept for the power-cut case.

**Permissions — the part that bites.** macOS Automation grants are per-app, per-machine, and per-binary: the Python that launchd runs is not the Python your terminal runs. A grant to Terminal proves nothing about the scheduled path. The framework interpreter needs its own Reminders grant, and the only honest proof is firing the real scheduled path on demand:

```
launchctl kickstart -p gui/$UID/com.nickrusso.dailydigest
```

— watched live with `tail -f` on the log. Green in a shell is not green at 7am.

**Consent sequencing.** Any OAuth scope change, and any new machine, requires interactive re-consent *before* the next scheduled run — otherwise the 7am job blocks on a consent prompt nobody is watching. Delete `token.json` first: a valid stale token silently bypasses the consent flow that carries the change. After consent, read the granted scopes back from the new token.

**Moving hosts.** `state.json` and the Reminders list are a matched pair, joined by reminder ids held in state. Fresh state against an intact list mints duplicate reminders — and ticks on the old copies resolve nothing. A host migration moves state in a single motion at the instant the old scheduler stops, and exactly one machine fires on any given morning; `token.json` never moves (see consent sequencing).

**Verify the install.** Five scripts run from a fresh clone with no credential file, no `token.json`, and no `state.json`: `test_render_zone.py`, `test_distinct_times.py`, `test_plan_parts.py`, `test_reminders_pipeline.py`, and `preview_digest.py`. Run each with the interpreter you installed the pins into. The four `test_` scripts end in `All checks passed` and exit 0; `test_reminders_pipeline.py` passes with no `state.json` present. `preview_digest.py` prints the fixture digests and exits 0 — it has no checks line.

## Operations

The morning health check is a glance, not a ritual — three instruments, each read for what it can actually prove.

**Pairing in `digest.out`.** Each run brackets itself with `RUN START` and `PRE-SEND` timestamps. No `RUN START` for the morning means one of two things: launchd never spawned the run, or the run died on import — `deliver.py` imports the pipeline before it prints `RUN START`, so that traceback lands in `digest.err`. A `RUN START` with no `PRE-SEND` means the run crashed mid-run. A pair means it reached the send. On this host, all 17 runs logged since 2026-08-30 are paired.

**The watchdog reads the pairing so a missed morning is not a silent one.** A second LaunchAgent, `com.nickrusso.dailydigest.watchdog`, fires at 07:30 and reads the last run in `digest.out`: it must carry today's date and be followed by `PRE-SEND` and then `Sent via Pushover`, the line `deliver.py` prints only after every part was accepted. A full pair is silent; anything less — no run, a crash before send, a send not confirmed, or an unreadable log — is a page under its own sender name and priority, three attempts a minute apart before it gives up and exits nonzero. A monitor that only speaks on failure is indistinguishable from a dead one, so on Sundays it sends a heartbeat carrying the week's paired count: if the Sunday page stops, the watchdog is what's down. It writes `WATCHDOG START` / `OK` / `PAGED` / `FAILED` to its own log and is read with the same `launchctl print` grep on its label.

**`launchctl print`, read at the top level only.** Nested keys sit at two tabs, so the grep anchors on exactly one:

```
launchctl print gui/$(id -u)/com.nickrusso.dailydigest | grep -E $'^\t(state|program|runs|last exit code) = '
```

`program` should name the framework interpreter; `state = not running` is the resting state between mornings; `last exit code = 0` means the last run exited cleanly.

**`digest.err` is a watermark, not a health check.** Its line count is currently 0. It grows on any write to stderr, including degradations the run survives by design — a synthesis attempt that gets retried, a reminder read or create that fails and is skipped, a reminder delete that fails after the resolution is already recorded. Growth means something is worth reading, not that the morning failed.

Delay and duration fall out of the pairing by subtraction. Measured on this host, 2026-08-30 to 2026-09-14: launchd's start delay was 2–6 s on the 15 scheduled runs (it varies by host; tens of minutes have been observed), and `RUN START` to `PRE-SEND` took 16–109 s across all 17 runs. Each reminder create adds 17–45 s — external-service latency is a distribution, not a number, and the timeouts are budgeted against measured bands, not guesses.

Synthesis writes its own lines to `digest.out`: `SYNTHESIS INPUT`, then `PREAMBLE`, `RAW`, and `RESULT` — or `FALLBACK` with a cause of `network` (every attempt failed to reach the API) or `validation` (the reply did not parse or did not pass `validate()`). `SYNTHESIS INPUT` logs the built records, including fields withheld from the model (`ref`, `end_time`, `end_zone`); it is not evidence of what the model saw. `capture_prompt.py` is. Since `073a076` the line carries those records minus `description_snippet` (the field stays in the record and in `PROMPT_FIELDS`, so the prompt is unchanged); mornings logged before that commit still carry it. The label is kept as it stands (ruled 2026-09-17) — a rename would break the readers silently against every morning already recorded, so the record is corrected in words instead. `PREAMBLE` is expected, not an alarm: the model writes reasoning ahead of its JSON, and the line records how many characters the parser discarded. Across the 11 synthesized runs through 2026-09-14: 11 `RESULT`, 0 `FALLBACK`.

Multi-part digests send in reverse, two seconds apart. Pushover's message list sorts newest-first, so sending the last part first makes the digest read 1, 2, 3 top-down — and the spacing forces distinct timestamps, without which arrival order is a coin flip. This deliberately encodes the client's display convention into the sender; the parts are numbered in-text, so a different display convention degrades to readable, not wrong.

The host is reachable from the road: Remote Login plus Tailscale, key auth supplied by the client's keychain. The pairing check compresses to one line from anywhere:

```
ssh <host> "grep -E '^(RUN START|PRE-SEND)' ~/Library/Logs/personal_assistant/digest.out | tail -2"
```

**Failure behavior is scoped.** Fetch and state stop loud: a failed fetch raises and kills the run before anything is saved, and a state file that won't parse is a hard stop, never a reset. Everything after the fetch degrades with a trace: a message whose extraction fails is skipped, listed in the run summary, and retried next run; synthesis falls back to an ungrouped page with a note (S6); a reminder create that fails is logged to `digest.err` and retried next run. The dominant failure class on record: the network is unavailable at run time and the run dies at OAuth token refresh — seven documented instances, the first four from captive-portal wifi leases expiring overnight in a hotel. Diagnosed from the logs alone, state verified untouched by mtime, no code change required. The failure class has never occurred on a home network.

## Verification approach

- **Fixtures over chance mail.** Tests run against self-authored fixture data through `preview_digest.py` — repeatable, free, and never dependent on what happened to arrive in the inbox. Its default path makes no network or API call: it renders the fixture corpus through the fallback, then replays a recorded synthesis grouping (`fixtures/grouping_fixture1.json`) at $0. `--live` is the only path that pays — one synthesis call that re-records the grouping. Tests never write live state; `test_reminders_pipeline.py` reads the real state file before and after to prove it.
- **A ruling ships with the cases that pin it.** `test_distinct_times.py` asserts the four cases behind the 2026-09-14 instants ruling — one flight stored in two zones plus a matching bare clock, a real disagreement in one zone, a bare clock matching nothing, and all-day only — through `_distinct_times` and both of its consumers, so the two cannot drift apart. It passes at `1cc90a4` and fails when the helper is reverted to counting clock strings. `test_render_zone.py` does the same for the 2026-09-27 render-zone rulings: eleven cases whose day header and line were pinned by script from a captured run, never typed in advance, so the renderer is the specification and a predicted string can't pass for a tested one. Changing one pinned header fails exactly that case.
- **Every delete claims proof.** A deletion isn't done until a read-back confirms the end state — a void call's success and its swallowed error are indistinguishable without one.
- **Tripwires where nothing should write.** Where a code path must not write, tests monkeypatch the writers to raise — absence of evidence is not evidence of absence.
- **The scheduled path is proven as itself.** `launchctl kickstart` fires the identical launchd-context run on demand; a passing shell run proves only the shell.

**Synthesis stability is measured across real mornings.** `replay_mornings.py` re-runs each recorded morning's synthesis input from `digest.out` through the current contract and compares group membership as `ref`-sets, so groupings are comparable across days however each morning numbered its records. Condensed from its output — the grouping recorded that morning against the grouping the current contract produces from the same input:

| Morning | Records | Recorded groups | Replayed groups | |
|---|---|---|---|---|
| 2026-09-04 | 17 | 7 | 6 | changed |
| 2026-09-05 | 25 | 8 | 6 | changed |
| 2026-09-06 | 16 | 5 | 4 | changed |
| 2026-09-07 | 15 | 7 | 5 | changed |
| 2026-09-08 | 15 | 7 | 5 | changed |
| 2026-09-09 | 15 | 7 | 5 | changed |
| 2026-09-10 | 17 | 10 | 6 | changed |
| 2026-09-11 | 24 | 10 | 9 | changed |
| 2026-09-12 | 27 | 13 | 9 | changed |
| 2026-09-13 | 18 | 10 | 10 | unchanged |
| 2026-09-14 | 19 | 9 | 9 | unchanged |

Mornings through 2026-09-13 were recorded under the first contract — the 2026-09-04 input is that day's 14:39 run, the first with synthesis — and 2026-09-14 was the first live morning under the rewrite. Every changed row is a journey collapsing, a stay's last day pairing with its check-out, or one drop-off reported by three sources — no merge crossed vendors, dates, or kinds. The Seattle return journey came back as the same `ref`-set on six consecutive mornings while the surrounding record count ranged from 15 to 24; on those same inputs the first contract had split it 4+2, 5+3, and 2+2+1+2. The 2026-09-13 row is the regression check: the rewrite left an already-correct morning alone. 2026-09-14 rendered as the replay predicted, and its replay after `1cc90a4` is unchanged — that commit touched conflicts, not membership.

Each morning was replayed once, except 2026-09-13, which was replayed twice and reproduced. The stability evidence is therefore cross-morning: the same `ref`-set for the same obligation under differing inputs, not repetition of one input.

**S4 reminder sharing is not yet exercised — by a test or by production.** `test_reminders_pipeline.py` covers reconcile and write-back without a grouping, one reminder per commitment. Production hasn't reached the path because reminders are minted only for NEEDS ATTENTION and TO DO, and every multi-member grouping to date has been COMING UP: 0 of 54 across the 11 synthesized runs had a todo or attention member.

## Known limitations

Found on real mornings, documented plainly.

**A correction made out of band never reaches the digest.** A change agreed by phone with a vendor lives in no system the assistant reads, so the digest keeps rendering what the sources still say — two real instances so far. This is the product statement working as specified: an obligation, or a correction to one, that exists in no read surface is never surfaced. It becomes visible when the vendor's email or a calendar edit lands.

**Calendar-written to-dos get no reminder, so there is nothing to tick.** Personal Organization — a separate planning project that writes deliberate obligations to Google Calendar; Calendar is the interface between the two — writes to-dos onto the calendar, and they arrive here as calendar events (`kind: event`). Reminders are minted only for commitments extracted from Gmail into NEEDS ATTENTION and TO DO (S3), and calendar data never enters state, so these render under COMING UP with no reminder attached.

**A genuine disagreement about a journey's departure time is no longer displayed.** Since 2026-09-16 a journey renders as two endpoint lines, and the "(sources disagree on time: …)" text is suppressed on those groups — a journey's legs carrying different clocks is the grouping working, not two sources contradicting each other. The cost is real and accepted: if two sources genuinely disagreed about when a journey departs, the page would show the primary's time and say nothing about the other. Every non-journey group still reports its disagreement.

**Journey detection depends on what extraction found.** A group renders as endpoints only when its members carry two or more distinct `FLIGHT ` identifiers, which `_FLIGHT_RE` pulls out of summaries and descriptions. A journey whose legs never yield two — a carrier the pattern doesn't name, a summary with no flight number in it — keeps the single range line it had before. It fails safe to the previous behavior rather than to a wrong line.

**Time-unknown obligations render with a placeholder time.** Same-day bookings carry a placeholder clock time, and the digest renders it as though it were known. There is no convention yet for an obligation whose time is genuinely unknown; it is on the design table.

**A trip label covers the whole day.** The container label renders as a day header — `DDD, Mon D — <name>: depart|return` — so every line on a trip's departure or return day sits under it, unrelated events included. The model assigns the label to groups; the renderer displays it by date.

**Near-duplicates can still render separately.** Message-level ingestion turns a thread of near-identical emails (booking, confirmation, reminder) into near-identical extractions. Synthesis folds them into one line when the contract's keys match and declines when they don't — an unmerged duplicate costs one line; a wrong merge hides an obligation. On the fallback path the collapse is narrower: two items fold only when they share a date and start time and a contiguous run of three normalized words (`collapse_display`, 2026-09-02) — byte comparison on normalized tokens, no similarity score. State keeps every record either way.

**Upstream writers are uncontrolled, and their data defects are not this system's to fix.** Google's travel parser has written instants an hour off three times on record. It was turned off on 2026-09-02, but the events it had already written remain. The renderer labels timed events with their zone when it differs from home, which closed the rendering side; a wrong instant rendered faithfully is still wrong on the phone, and the fix is upstream, in the calendar. The discipline this encodes: separate "is the code right" from "is the data right," then check the data.

**The timeline sorts by clock face, not by instant.** Every timed line is keyed on its naive local date and clock, in whatever zone the endpoint carries — `merge_coming_up` has keyed items that way since the day-block register — so two lines on one day in different zones can print out of true order: an event at 9:00 PM EDT happens two hours before one at 8:00 PM PDT, yet prints after it. Pre-existing and unchanged by the endpoint renderer, which keys its Arrival line the same way deliberately rather than introducing a second convention. Backlogged with the UTC/timezone work, where the instant-versus-clock question gets ruled once for the whole timeline.

**A gmail-sourced departure renders without a zone.** Gmail commitments carry no zone at all, so when a journey's primary is a gmail member (since 2026-10-03, only when no leg carries a stored zone: selection term 0 sorts a bare clock after every instant), the Departure line prints a bare clock — which implicitly claims the home zone, right or wrong. The Arrival is unaffected: it labels from its own zone whenever that isn't home. Since 2026-09-27 an ordinary (non-journey) gmail line is labeled in the zone synthesis names for its group, so this remains true for journey Departures, and for any gmail line whose group got a null render zone.

**Journeys and conflict text still render in the zone stored on the record, right or wrong.** Since 2026-09-27 an ordinary timeline line renders in its group's `render_zone` / `end_render_zone` — the zones synthesis reads off the places the records name, admitted through `ZoneInfo` — so the shape of the 2026-09-18 outbound, stored as New York at both ends for a Dallas arrival, renders `2:24 PM EDT–4:13 PM CDT` in its fixture twin. Journey groups (two or more flight numbers) are outside that path: their Departure and Arrival lines still read record zones, as does the "(sources disagree on time: …)" text. A group with a null render zone renders from the stored zone as before. Proven on the scheduled path by the 2026-09-27 17:32 kickstart (runs 32, exit 0, `digest.err` 0), where the first live `render_zone` rendered on a home-zone group; the non-home branches — convert, label, the cross-zone range — remain proven by fixture only until a non-home morning.

**A converted line crossing local midnight stays under its stored day.** Converting a zoned event into its render zone can move it past midnight — 11:30 PM Pacific is 2:30 AM Eastern the next day — but the line keeps its stored date and sort key, so it prints under the earlier day's header. Accepted (ruled 2026-09-27) and pinned as-is by `test_render_zone.py`; re-dating converted items is backlogged beside UTC/timezone.

**A gmail clock written in the sender's home zone is labeled as the venue's.** A gmail appointment carries a bare clock and no zone, and the renderer treats it as the venue's wall clock, labeling it with the render zone. If the sender wrote the time in their own zone instead, the label is wrong. The data cannot tell the two apart; converting instead would fail the same case the other way. Accepted (ruled 2026-09-27), no heuristic.

**A range with a partial zone answer is not converted at all.** A zoned event with an end converts only when both of its render zones resolve; if the end's is null or invalid, the whole line keeps its stored zones. The line may be less local than it could be, but it never mixes a model zone and a stored zone on one line (ruled 2026-09-27).

**The "gmail with a captured zone → convert" branch is unreachable today.** The label-or-convert discriminator is on the data — a stored zone or none — not on the source, but `build_records` passes no zone for any gmail commitment, so every gmail line takes the label branch. A future extractor change that captures zones lands in convert by design, untested until then.

**Airport-code endpoints are table-backed; the correction has not yet been seen live.** On the 2026-09-11 morning a gmail-only flight named only by two airport codes (TPA to DFW, no location) drew a start zone of New York or null from the model on an unchanged request — null on 10 of 16 short replies on 2026-09-26. Three input variables were varied and none moved it, so the fix left the prompt alone: since 2026-09-27 a group whose every member names its endpoints only as `CODE to CODE` (exact token test, empty location) takes both zones from `airports.py`, a hand-checked IATA → IANA table, after the reply parses and before validation. The correction of a null or wrong model zone is proven only against a canned reply: across 69 live calls that day (fixtures 7/7 on three byte-identical passes, 22 mornings × 3) the table backed 15 groups — r24 and the 09-13 return flight on each corpus pass, fixtures (b), (f1), (f2) on each fixture pass — and the model had already written the table's zones on all 15, so the override replaced each value with the same value. A code the table lacks keeps the model's zone and logs `SYNTHESIS ZONE` naming it — the table can be incomplete, but not silently. Known gap, accepted: "Tampa TPA to DFW" also matches, since telling a city from a code would take a list of cities; the table and the city agree unless the record contradicts itself. Endpoints named in words (cities, hotels, addresses) are still the model's to name.

**The synthesis reply has two length modes, and the long one can hit the token cap.** On a byte-identical request the reply is usually short, but on 2026-09-26 one burst of eleven calls came back 1722–4000 output tokens; three hit `MAX_TOKENS = 4000`, their JSON was cut, and they fell back to the ungrouped page. It did not recur in the next nine calls or in the 69 on 2026-09-27 (highest 1998). The cap stays at 4000: what fills a long reply has never been captured, and raising the cap before that is known would be tuning without attribution. The tripwire is `stop_reason` on the `SYNTHESIS FALLBACK` line — a truncated morning degrades to a fallback page that says why in the log; it does not fail silently.

**Calls and virtual meetings render in the zone stored on the record.** The 2026-09-13 exception — a call renders in Nick's zone for that day, not the organizer's — is out of scope for "finished" and documented here instead. `location_on_day`, which it depends on, is not built. Reopen trigger: the first real call or virtual meeting on the page.

**Two primary-selection rules coexist.** On a synthesized morning code selects the primary by the ordered rule above; on an S6 fallback morning there are no groups, and `collapse_display` keeps its 9/2 survivor rule, the longest summary — so a fallback morning can still lead a line with a gmail copy's bare clock. The model's own proposal still drifts across runs; it no longer reaches the page, and `SYNTHESIS PRIMARY` records every time it is overruled. Unifying the two rules, and then retiring rule 9 from the prompt, is one backlog item.

**The watchdog cannot catch a shared-cause failure.** It covers the no-spawn and crash classes when the mini is up and the network is back by 07:30. Three shapes stay silent: the watchdog itself not spawned (whatever refused the digest's agent likely refused this one), the network down at 07:30 as at 07:00 (it detects correctly and cannot page), and the mini off. The Sunday heartbeat turns the first into a weekly human check; the only design that catches the third inverts the signal — the digest pings an external dead-man's switch that pages on absence — and that adds a third-party dependency and a credential, so it is backlogged as its own design question rather than bolted on.

**State grows without bound.** Resolved and auto-dropped records persist forever. Harmless at current volume, unaddressed by design priority, not by oversight.

**Retired 2026-09-14: time conflicts compared clock strings.** One flight stored as 19:30 America/New_York by one writer and 18:30 America/Chicago by another rendered as "sources disagree on time" — one instant under two labels. Fixed in `1cc90a4`: conflicts compare instants, and `test_distinct_times.py` pins the cases. Listed so the record of what was wrong survives the fix.

## Roadmap

Sequenced by reasoning, each behind its gate:

1. **Log hygiene** — `description_snippet` out of the `SYNTHESIS INPUT` log line shipped 2026-09-21 in `073a076`: `_log_records` drops the key on the way to the log, and the field stays in the record and in `PROMPT_FIELDS`, so the prompt is unchanged. The cost is a fact now rather than a trade — a replay of a morning logged after that commit reproduces less than the live run saw. Still open is the measurement that replay was going to feed: whether the snippet belongs in the model's input at all. It is runnable without waiting on anything, by stripping at read time against the corpus logged through the 2026-09-21 07:00 run — the last mornings whose `SYNTHESIS INPUT` lines carry the snippet
2. **A watchdog agent** — the one failure class no in-run notifier can see is the run that never spawns or dies on import: no `RUN START`, no alert. A monitoring channel cannot monitor itself. An independent agent fires after the window, reads the log for a today-stamped `RUN START`, and alerts on absence

Ruled, not built:

- **S4 reminder-sharing test** — sharing in write-back and the one-read-per-id reconcile are exercised by neither a test nor production yet (see [Verification approach](#verification-approach))

Built 2026-09-27 and proven on the scheduled path by that day's 17:32 kickstart (runs 32, exit 0, `digest.err` 0; first live `render_zone` on a home-zone group). The non-home branches are proven by fixture only until a non-home morning:

- **Zone-of-render** (requirement ruled 2026-09-22, mechanism 2026-09-24, synthesis side built 2026-09-26, renderer built 2026-09-27) — every ordinary timeline line shows local time where the event takes place. The stored zone can be wrong (the 2026-09-18 outbound carried New York at both ends for a Dallas arrival), so synthesis supplies the zone: rule 11 asks for IANA keys read off the places the records name, airport-code endpoints take theirs from `airports.py`, and code nulls and logs (`SYNTHESIS ZONE`) any key `ZoneInfo` rejects. The renderer converts a zoned calendar line into those zones (all or nothing on a range) and labels a gmail clock with them; a null zone renders from the stored zone as before. Display-only — conflicts, identity, state, and Reminders still compute from stored zones. Journeys are outside it (see Known limitations)
- **Zoned-member tiebreak** (ruled 2026-09-22, built 2026-09-27) — inside a synthesized, non-journey group whose primary has no stored zone, the one member with a zoned start at the same instant (compared in the render zone) supplies the line. Display-only: the group's recorded primary is never rewritten. Unreachable on synthesized groupings since primary selection moved into code (2026-10-03): an unzoned primary now means no member is zoned

On the design table, each needing a ruling before build:

- **Duplication display-collapse** (backlog #1) — the fallback collapse shipped 2026-09-02, and negative fixture 4 and the case-sensitivity ruling on the containment discriminator closed with it the same day; the item itself closed 2026-09-22 on the Fort Worth evidence — 42 obligations on 42 lines across seven mornings on the current contract (`7228e20`), against Seattle's 59 lines for 49 obligations on the old one. Reopen trigger: any morning on the current contract where one obligation — one booking, one event — renders on two lines. Two confirmations for the same trip leg are two obligations and do not trigger it
- **Flight-number exact-key pre-join** — parked 2026-09-22: whether an exact-key pre-join (flight number + date, byte-equal) belongs ahead of synthesis is not built while the current contract holds. If a morning on the current contract shows one flight split across two groups, this is the first candidate fix, and that same morning reopens backlog #1
- **Implausibility detection** — the synthesizer is the only stage that sees a whole day at once and could notice a car pickup that precedes the flight that gets you there — but that asks for a judgment, and judgments are where conflicts left the model on 2026-09-04. A real tension, not an obvious win
- **Time-unknown convention** — for obligations whose time is genuinely unknown (see Known limitations)
- **Cancellation entry** — a cancellation is obligation-shaped and needs a read surface; Gmail can't carry it (confirmations are permanent), Calendar can. One shape: a Calendar record the synthesizer treats as negating the thing it names. Which record it negates is an identity question and gets ruled, not defaulted
- **iMessage as a source** — gated behind a privacy ruling: a contact allowlist decision before any message content reaches the extraction API
- **Cloud hosting** — hard blocker: Reminders (osascript) and iMessage both require local macOS
- **UTC/timezone** — the instant-versus-clock question, ruled once for the whole timeline
- **Re-date converted items** — a line converted across local midnight keeps its stored date (see Known limitations); moving it touches `sort_key`, the day headers, and the window edge, so it belongs with UTC/timezone
- **S6 zoned-survivor tiebreak** — on a fallback morning there are no groups, and `collapse_display` keeps the longest summary (9/2). The candidate fix is a byte-exact "item carries a zoned start" test inside `collapse_display`, cheap now that items carry their source data
- **One selection rule** — unify `_select_primaries`, `collapse_display`'s survivor rule, and the S6 zoned-survivor line into one exact rule; rule 9 then becomes removable from the prompt, and the zoned-member tiebreak, unreachable on synthesized groupings since 2026-10-03, can go
- **Air-removal ruling** — the decommissioned MacBook Air keeps an installed-but-unloaded plist as rollback; whether to remove it entirely is unruled
- **Additional sources** (Google Tasks, manual entry) — blocked on a source-labeling convention for multi-source item lines
- **Operational hardening** — state retention, log rotation
