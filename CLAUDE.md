# CLAUDE.md — Personal Assistant

*Written 2026-09-04 when build sessions moved from claude.ai Chat to Claude Code. Derived from the Personal Assistant project instructions (revised 2026-08-31). The project's session summaries and design records live in `../personal_assistant_docs/` and are not tracked; read the newest summary first, and when it points backward, read the earlier ones before reconstructing.*

## What this is

A production system that reads Gmail and Google Calendar, extracts commitments via the Claude API, tracks open/resolved state across days, and delivers a digest at 7:00 AM via Pushover, with resolution from the phone via a dedicated Apple Reminders list. It runs unattended, daily, on this Mac mini via a LaunchAgent labeled `com.nickrusso.dailydigest`. **This repo is live production.** Nick rules design questions explicitly; Claude does not rule by default. Rulings are recorded in the session summaries and carry forward.

## Product statement (constitution — do not dilute)

The assistant reports obligations that already exist; it never generates its own. No imperatives anywhere in the deliverable: the digest states what exists, it never instructs. An obligation that lives in no system the assistant reads is never surfaced.

**Granularity principle (2026-09-13):** "The purpose of the digest isn't to map out every second of my day, but to give me the information I need for the day and the next seven days, based on all of my extracted sources." Working test: does this line change what Nick would do at 7 AM? Granularity is a separate axis from the source rule ("report only what exists") and from dedup.

## Hard rules for Claude Code in this repo

- **Never run `digest.py`.** It is the production run: it writes `state.json`, creates Reminders, and sends Pushover. Production runs only via the LaunchAgent, or via a `launchctl kickstart` that Nick performs while watching.
- **`preview_digest.py` is the sanctioned test surface.** It is $0 and network-free by default. Do not add network or API calls to its default path.
- **Never read, edit, copy, or regenerate `state.json`.** It is a matched pair with the Reminders list via iCloud reminder ids. Fresh state against an intact list mints duplicates. It is gitignored and must stay so.
- **Never touch `token.json`, `~/.personal_assistant.env` (or any `.env`), or the client secret.** Never print their contents. Never run `probe_calendar.py` (it carries its own `SCOPES` list and overwrites `token.json`).
- **Never write to Google Calendar.** OAuth scope is `calendar.readonly`. Any scope widening reopens a design question and requires interactive re-consent before the next scheduled run.
- **Never create synthetic events or reminders in production.** Tests use self-authored fixtures in `preview_digest.py`, never chance mail.
- **Never `git commit` or `git push` unless Nick says to, in that session.** Git runs in a standalone Terminal by convention; if Nick asks Claude Code to commit, verification lands before the commit or the commit message says `provisional`.
- **Never modify the LaunchAgent plist or run `launchctl` mutations** (`bootstrap`, `bootout`, `enable`, `disable`, `kickstart`). Reading with `launchctl print gui/$(id -u)/com.nickrusso.dailydigest` is fine — always the exact label, never a discovering grep.
- **Production interpreter is `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`** (python.org framework build), never Homebrew. Run everything with it. Automation/TCC grants attach per binary; a Terminal pass proves nothing about the scheduled path.
- **Credentials and PII never appear in output.** If a fixture or log carries a confirmation code, reservation number, name, or phone number, say so and do not repeat it.

## Standing design rulings (binding; reasoning in the summaries)

- **Read-only boundary:** Gmail read, Calendar read. The one sanctioned write surface is the dedicated `Daily Digest` Reminders list.
- **Identity is `(gmail message id, array position)`**, stored as `"<id>:<position>"`. Changing the ingestion unit changes the identity scheme — that is a project, not a fix.
- **Fuzzy matching is banned on identity** — anything that resolves, deletes, or merges records. Deliberately widened once (S4, 2026-09-03) to the reminder-minting path, with guardrails: partition check, grouping log, members add-only. Display-layer collapse is a separate, bounded question.
- **"One stop shop":** never suppress an obligation because another system tracks it. Dedup collapses display, never data.
- **Reminders:** deletion means nothing; only `completed = true` on the exact stored id resolves. Title is display-only, id is identity. The single delete site is `resolve_one` in `resolve.py`.
- **Timeouts on writes raise, never retry.** Fetches and the synthesis call are pure reads; bounded retry there does not reopen this.
- **Synthesis (2026-09-03, S1–S6):** one Claude API call per run over the day's records returns a *grouping* — structure plus one label. The trip container name is the only model-authored text that reaches the page; the prompt forbids inventing it, and code checks only that it is a non-empty string. Code validates (partition, schema, one date per group, no do/happen mixing). The renderer builds every line from the primary record's real fields. When uncertain, do not merge — except when rule 2 or rule 3 of the contract is met (same date plus a shared confirmation code, reservation number, or flight number, or plainly the same vendor and the same thing): those keys are decisive, not judgment calls (rule 8 in `synthesize._prompt`). Synthesis never costs the morning: on failure, render the `collapse_display` fallback with a note. S2: multi-day spans render endpoints only. S3: Reminders for TO DO / ATTENTION only. S4: one reminder per obligation across sources (members share a `reminder_id`). S5: day-block register, `DDD, Mon D — <name>: depart|return`.
- **Times:** an event renders in its own stored zone with the abbreviation when not `HOME_TZ`. The Calendar API formats `dateTime` in the calendar's zone on read; the writer's digits do not survive storage, so the reader cannot recover a wrong instant — upstream data errors are fixed upstream. Physical events display in the zone stored on the record; calls and virtual meetings currently do too, because nothing distinguishes them. The unruled remainder (calls/virtual meetings in Nick's zone for the day, `location_on_day`) is under Zone-of-render below.
- **One journey, one obligation (2026-09-13).** Records sharing a confirmation code on one date are one obligation, regardless of differing flight numbers, airports, or times. Supersedes the 2026-09-04 rule that two legs are two groups. A same-day out-and-back under one booking code collapsing to one entry is accepted deliberately. It rendered as one line until 2026-09-16, when endpoints replaced the single line — see Journeys render as endpoints; one obligation is unchanged, only how many lines it earns.
- **Time never splits (2026-09-13).** Time, zone, wording, phrasing, and source are never identity discriminators. Time disagreement is reported by code (`derive_conflicts`), never used to separate records.
- **Different date always splits (2026-09-13).**
- **Time conflicts compare instants, not clock strings (2026-09-14, shipped in `1cc90a4`).** Zoned members compare as UTC; a zone-null (gmail) member agrees when its clock matches any zoned member's clock, otherwise it counts as distinct. `_distinct_times` in `synthesize.py` is the single helper behind both `derive_conflicts` and `filter_time_conflict`.
- **`(×N)` marker retired (ruled 2026-09-14, removed 2026-09-15).** The page reports; membership correctness is the synthesis contract's job, verified by `replay_mornings.py` and `synth_dump.py`, never by a counter on the consumer surface. If something is swallowed, the fix is a contract change. Gone from both render paths in `digest.py`: `_mark` is deleted and `collapse_display` appends its survivor unchanged.
- **Identity is code's domain (2026-09-14).** The model sees the record fields `_prompt` sends and groups on obligation-level evidence; which of those fields are decisive is the contract's job (rules 2–3), and which never discriminate is rule 5's. The model never sees a pipeline key — anything the pipeline mints or stores to find a record again: `ref`, gmail message ids, reminder ids. The per-run id (`r1`…) is a per-call handle, not a key. `PROMPT_FIELDS` in `synthesize.py` is the allowlist (built 2026-09-15, `0018b87`): withhold-by-default is live, and `end_time`/`end_zone` are the first fields it holds back (`c313594`).
- **Journeys render as endpoints (2026-09-16).** A journey group — two or more distinct `FLIGHT ` identifiers among its members, exact string comparison — renders two lines: Departure from the primary's start, Arrival from the member whose end instant is latest (tie: the primary if it is among them, else first in member order). Both endpoints carry zone labels when the departure and arrival zones differ. No member carrying an end: Departure only. The Arrival is its own timeline item, dated and sorted by the arrival, and `(sources disagree on time: …)` is suppressed on journey groups — legs carrying different clocks is the grouping working, not two sources contradicting each other. Display-only: built in `render_coming_up`, reaching neither state, identity, nor Reminders.
- **The frame rule (2026-09-16).** In the renderer a record may supply a clock, a zone, and a day *difference* — never an absolute date. Absolute dates come from merged items only. A recorded grouping replays against items rebuilt today, so record dates sit in the frame they were recorded in; reading one put an Arrival line three days behind its own trip, under a day header of its own.
- **Zone-of-render is NOT ruled.** Intent recorded 2026-09-13: events render in the zone where they occur, except calls/virtual meetings, which render in Nick's zone for that day. Blocked on a `location_on_day` fact that does not exist yet. Backlogged with UTC/timezone.

## Working rules

- **Read before patch.** Read the target file as it is on disk before proposing or applying a change. Read a diagnostic script before running it.
- **Every change gets a read-back** before it is called done. Every delete claim gets a read-back.
- **Matchers are built against captured stderr, never predicted.** Instruments carry literal identifiers, never discover them.
- **Measured once is not measured.** Stability of anything model-dependent means three identical results.
- **Separate "is the code right" from "is the data right," then check the data.** An upstream writer's wrong instant is not a renderer defect. A plausible story fitted to anomalous data is not a diagnosis.
- **Verification lands before the commit, or the commit is provisional.**
- **Where nothing should be written, tripwires beat absence of evidence.**
- **When a design question surfaces mid-task, stop and ask Nick.** Do not pick a default and keep going.
- **Instruments identify themselves.** Every diagnostic prints `hostname`, `pwd`, and `git log -1` as a header. Output with no provenance cannot be distinguished from another machine or checkout.
- **A library function must never print unconditionally.** `synthesize._log` leaked an unmasked `SYNTHESIS INPUT` through `replay_mornings.py`; harnesses wrap it with `contextlib.redirect_stdout`.
- **Long text goes through a file,** never an inline shell argument or a heredoc typed by hand. Commit messages use `-F`.
- **When output is an arbitration, the specification is the defect.** A capable model given contradictory rules picks one, and which one depends on context it was never told to ignore. Look for the contradiction before concluding the model judges badly.
- **Naming the non-discriminators is as load-bearing as naming the decisive keys.** Silence is not neutrality.

## Instruments

- Logs: `/Users/nickrusso_macmini/Library/Logs/personal_assistant/digest.out` and `digest.err`. Pairing `RUN START` with `PRE-SEND` in `digest.out` separates the failure cases from clean. No `RUN START`: never spawned, or died on import (`deliver.py` imports `digest` before it prints `RUN START`; the traceback lands in `digest.err`). `RUN START` with no `PRE-SEND`: crashed mid-run. A pair: clean through the send. `digest.err` is a watermark (currently 0), not a health check. `SYNTHESIS INPUT|PREAMBLE|RAW|RESULT|FALLBACK` lines land in `digest.out`. `PREAMBLE` records the characters of reasoning the parser discarded ahead of the JSON; it is expected, not an alarm (present on every synthesized run through 2026-09-14).
- `launchctl print gui/$(id -u)/com.nickrusso.dailydigest | grep -E $'^\t(state|program|runs|last exit code) = '` — the exact-indent anchor; nested keys sit at two tabs.
- Dominant failure class: `NameResolutionError` on `oauth2.googleapis.com` at run time (network unavailable). Signature: `RUN START` with no `PRE-SEND`, exit 1.

## Quality bar

Showcase-grade: the repo should be presentable to potential employers. The reasoning is the portfolio piece, not the line count. Docstrings carry the ruling and its date. Honest limitations sections are themselves showcase material.
