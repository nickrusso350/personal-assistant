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
- **Times:** an event renders in its own stored zone with the abbreviation when not `HOME_TZ`. The Calendar API formats `dateTime` in the calendar's zone on read; the writer's digits do not survive storage, so the reader cannot recover a wrong instant — upstream data errors are fixed upstream. Physical events display in the zone stored on the record; calls and virtual meetings currently do too, because nothing distinguishes them. What remains open is under Zone-of-render below: the requirement is ruled, the mechanism is not — a stored zone can be wrong — and `location_on_day` is a dependency of the calls/virtual-meetings exception only.
- **One journey, one obligation (2026-09-13).** Records sharing a confirmation code on one date are one obligation, regardless of differing flight numbers, airports, or times. Supersedes the 2026-09-04 rule that two legs are two groups. A same-day out-and-back under one booking code collapsing to one entry is accepted deliberately. It rendered as one line until 2026-09-16, when endpoints replaced the single line — see Journeys render as endpoints; one obligation is unchanged, only how many lines it earns.
- **Time never splits (2026-09-13).** Time, zone, wording, phrasing, and source are never identity discriminators. Time disagreement is reported by code (`derive_conflicts`), never used to separate records.
- **Different date always splits (2026-09-13).**
- **Time conflicts compare instants, not clock strings (2026-09-14, shipped in `1cc90a4`).** Zoned members compare as UTC; a zone-null (gmail) member agrees when its clock matches any zoned member's clock, otherwise it counts as distinct. `_distinct_times` in `synthesize.py` is the single helper behind both `derive_conflicts` and `filter_time_conflict`.
- **`(×N)` marker retired (ruled 2026-09-14, removed 2026-09-15).** The page reports; membership correctness is the synthesis contract's job, verified by `replay_mornings.py` and `synth_dump.py`, never by a counter on the consumer surface. If something is swallowed, the fix is a contract change. Gone from both render paths in `digest.py`: `_mark` is deleted and `collapse_display` appends its survivor unchanged.
- **Identity is code's domain (2026-09-14).** The model sees the record fields `_prompt` sends and groups on obligation-level evidence; which of those fields are decisive is the contract's job (rules 2–3), and which never discriminate is rule 5's. The model never sees a pipeline key — anything the pipeline mints or stores to find a record again: `ref`, gmail message ids, reminder ids. The per-run id (`r1`…) is a per-call handle, not a key. `PROMPT_FIELDS` in `synthesize.py` is the allowlist (built 2026-09-15, `0018b87`): withhold-by-default is live, and `end_time`/`end_zone` are the first fields it holds back (`c313594`).
- **Journeys render as endpoints (2026-09-16).** A journey group — two or more distinct `FLIGHT ` identifiers among its members, exact string comparison — renders two lines: Departure from the primary's start, Arrival from the member whose end instant is latest (tie: the primary if it is among them, else first in member order). Both endpoints carry zone labels when the departure and arrival zones differ. No member carrying an end: Departure only. The Arrival is its own timeline item, dated and sorted by the arrival, and `(sources disagree on time: …)` is suppressed on journey groups — legs carrying different clocks is the grouping working, not two sources contradicting each other. Display-only: built in `render_coming_up`, reaching neither state, identity, nor Reminders.
- **The frame rule (2026-09-16).** In the renderer a record may supply a clock, a zone, and a day *difference* — never an absolute date. Absolute dates come from merged items only. A recorded grouping replays against items rebuilt today, so record dates sit in the frame they were recorded in; reading one put an Arrival line three days behind its own trip, under a day header of its own.
- **The `SYNTHESIS INPUT` label stays (2026-09-17).** No relabel. The label is misleading — the line logs the *built records*, including `ref`, `end_time`, and `end_zone`, none of which reach the model — but a rename breaks both readers silently against every morning already recorded: `replay_mornings.py` compares the kind token by equality (line 26) and `synth_dump.py` keys on it (lines 47, 106, 126), and a missed match renders as "NO INPUT" or a zero-record dump, which is exactly what a genuine absence looks like. The record is corrected in words instead: README and this file now say what the line is, and name `capture_prompt.py` as the evidence of what the model actually saw. The matching sentence landed in `synthesize._log`'s docstring on 2026-09-21, in `073a076`, with the rest of R3's deferred batch.
- **`_log` is conditional — ruled 2026-09-17, BUILT 2026-09-21.** Logging stays ON by default; quiet callers pass `log=False`, which silences all six kinds (`INPUT`, `PREAMBLE`, `RAW`, `RESULT`, `FALLBACK`, and since 2026-09-25 `ZONE`) at all nine call sites. `synthesize` and `_call_model` both take `log=True`; `digest.py:746` was not edited, so the scheduled path keeps logging by omission rather than by argument. `preview_digest.py`'s `--live` path now passes `log=False`: it had no wrapper and printed an unmasked `SYNTHESIS INPUT` to the terminal. `replay_mornings.py` keeps its `contextlib.redirect_stdout` wrapper, unedited (ruled 2026-09-21) — the switch does not oblige a working harness to change.
- **The hygiene batch splits by file (2026-09-17) — both halves LANDED.** Commit one touched nothing the 7:00 run executes (`replay_mornings.py`, `synth_dump.py`, `README.md`, `CLAUDE.md`) and landed 2026-09-17 in `dbba771`. Everything inside `synthesize.py` waited until after 2026-09-20 and landed 2026-09-21: the `description_snippet` out of the log line, the `log=` switch (R2), the stale module docstring, and the `_log` docstring sentence (R1). Reason for the split: Nick was away three mornings and no build work happens over SSH. A change to the module the scheduled run imports lands when someone is at the keyboard the next morning.
- **`end_time`/`end_zone` show unmasked in `synth_dump.py` (2026-09-17).** They joined `KEEP`, positioned after `zone`, and are not scrubbed or truncated: a clock string and an IANA zone name carry nothing to mask, and they are what a conflict diagnosis reads. Until this they fell through to the unexpected-keys NOTE, which is the tripwire working as designed — a field added to a record gets classified deliberately, in `KEEP` or in `DROP`, never by silence.

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
- **A library function must never print unconditionally.** `synthesize._log` leaked an unmasked `SYNTHESIS INPUT` through `replay_mornings.py`. Since 2026-09-21 the switch is in the library: `synthesize(..., log=False)` silences every line, and a caller that must stay quiet asks by argument. `contextlib.redirect_stdout` stays where a ruling keeps it (`replay_mornings.py`), belt and braces, not as the only guard.
- **Long text goes through a file,** never an inline shell argument or a heredoc typed by hand. Commit messages use `-F`.
- **When output is an arbitration, the specification is the defect.** A capable model given contradictory rules picks one, and which one depends on context it was never told to ignore. Look for the contradiction before concluding the model judges badly.
- **Naming the non-discriminators is as load-bearing as naming the decisive keys.** Silence is not neutrality.

## Instruments

- Logs: `/Users/nickrusso_macmini/Library/Logs/personal_assistant/digest.out` and `digest.err`. Pairing `RUN START` with `PRE-SEND` in `digest.out` separates the failure cases from clean. No `RUN START`: never spawned, or died on import (`deliver.py` imports `digest` before it prints `RUN START`; the traceback lands in `digest.err`). `RUN START` with no `PRE-SEND`: crashed mid-run. A pair: clean through the send. `digest.err` is a watermark (currently 0), not a health check. `SYNTHESIS INPUT|PREAMBLE|RAW|RESULT|FALLBACK|ZONE` lines land in `digest.out`. `SYNTHESIS INPUT` logs the built records, including fields withheld from the model (`ref`, `end_time`, `end_zone`); it is not evidence of what the model saw — `capture_prompt.py` is. `PREAMBLE` records the characters of reasoning the parser discarded ahead of the JSON; it is expected, not an alarm (present on every synthesized run through 2026-09-14).
- `launchctl print gui/$(id -u)/com.nickrusso.dailydigest | grep -E $'^\t(state|program|runs|last exit code) = '` — the exact-indent anchor; nested keys sit at two tabs.
- Watchdog (2026-10-01): `com.nickrusso.dailydigest.watchdog` runs `watchdog.py` at 07:30, framework Python, logs to `watchdog.out` / `watchdog.err` beside the digest's. It reads the *last* `RUN START` in `digest.out`, requires today's local date on it, and requires `PRE-SEND` and then `Sent via Pushover` (`deliver.py`, after the send loop) in the same run — so **three lines are byte-for-byte now, not two**. Four page classes: file missing/unreadable, no `RUN START`, no `PRE-SEND`, no `Sent via Pushover`; a full pair is silent. Pages go to a separate Pushover application (`PUSHOVER_WATCHDOG_TOKEN`), priority 1, three attempts a minute apart, then `WATCHDOG FAILED` and exit 1. Sundays send a priority-0 heartbeat with the week's paired count; a Sunday failure folds the count in (one request per run). Own log lines: `WATCHDOG START` / `OK` / `PAGED` / `FAILED`. Open ritual adds `launchctl print gui/$(id -u)/com.nickrusso.dailydigest.watchdog` through the same grep. Not covered, by design: the watchdog itself not spawned (a shared cause refuses both agents), network still down at 07:30, mini off — the heartbeat makes the first visible weekly; mini-off needs an external dead-man's switch (backlog). Test surface: `--log <fixture> --date YYYY-MM-DD --dry-run`; dry-run never reads the env and cannot send; the live `digest.out` is never a fixture.
- Dominant failure class: `NameResolutionError` on `oauth2.googleapis.com` at run time (network unavailable). Signature: `RUN START` with no `PRE-SEND`, exit 1.

## Quality bar

Showcase-grade: the repo should be presentable to potential employers. The reasoning is the portfolio piece, not the line count. Docstrings carry the ruling and its date. Honest limitations sections are themselves showcase material.

## Zone-of-render — ruled 2026-09-24 (Option D: synthesis supplies the zone)

- Mechanism: synthesis returns per-group `render_zone` and `end_render_zone` as IANA keys (e.g. `America/Chicago`), not place names. No repo-kept airport/city table.
- Code validates each against `ZoneInfo`; anything invalid is nulled and logged. The model proposes, code admits.
- Display-only. Never into `_distinct_times`, `derive_conflicts`, identity, state, or the Reminders path. Conflicts stay code-derived from stored zones (9/13 ruling holds).
- Precedence: record text wins over the stored zone for rendering. Stored zone remains what conflicts and instants are computed from.
- Fallback: null or invalid key renders from the stored zone as today, no mark on the page. Tiebreak (b) — the zoned member — is the floor on an S6 fallback morning.
- The model infers from whatever the records say (codes, cities, addresses, hotel names) and writes null when nothing names a place. No manual list.
- Calls/virtual-meetings exception (9/13, second half): out of "finished" scope as a documented limitation. Reopen trigger: the first real call or virtual meeting on the page.
- Build order: (1) synthesis side only — prompt rule after rule 10, parser admits the two fields, `PROMPT_FIELDS` unchanged, self-authored fixtures with expected zones written before the run, three byte-identical replay passes (two of three is a failure), one commit, no renderer change, no kickstart. (2) path (a) injection plus tiebreak (b), with kickstart.
- Documented input class (9/24): self-sent mail as transport between projects. First candidate fix on recurrence: from-self filter at fetch.

### Ruled 2026-09-26 — synthesis side commits; path (a) gated

- **Commit criterion:** zero fallbacks, zones identical across three passes, and membership matching the recorded RESULT on every morning from 09-13 onward. Primary drift is pre-existing and not a commit blocker.
- **`MAX_TOKENS` 2000 → 4000.** The 9/25 replay fallbacks were truncation: on 09-05, 2 of 3 calls stopped at `stop_reason == "max_tokens"` with exactly 2000 output tokens and the JSON cut mid-group, and every fallback in those nine calls was a `max_tokens` stop. A cut reply surfaces as `grouping is not an object with a groups list`, not `not valid JSON`: `_last_json_object` finds the last complete *inner* group and `validate` rejects it.
- **`SYNTHESIS FALLBACK` carries `stop_reason`** alongside `cause` and `error` (`null` on the network fallback, where no reply exists). `_call_model` returns `(text, stop_reason)`.
- **Measured:** old prompt (`7130547`) ceiling 1595/2000 output tokens over 42 calls (14 mornings × 3), zero fallbacks. Current prompt ceiling 2140 on 09-12, over 66 calls at 4000 (22 mornings × 3) — 09-12 also reached 2005, so two of 66 would have truncated at the old limit. Fixtures 5/5 on three byte-identical passes at 4000. Corpus at 4000: 0 fallbacks, membership 39/39 against the recorded RESULT for 09-13 through 09-25, zones identical across passes on 21 of 22 mornings.
- **Primary drift is pre-existing (rule-9 tightening, its own backlog item).** Same 14 mornings, three passes: old prompt 12 mornings / 18 groups flipped primary; current prompt at 2000, 11 / 17 (fallback passes excluded). At 4000 over all 22 mornings: 9 / 13.
- **Known rule-11 defect, verbatim from the ruling:** The 09-11 r24 start-zone wobble (New York 4 / null 4 across nine calls, a gmail-only codes-only flight, fixture case (b)'s live twin) is recorded as a known rule-11 defect, page-invariant on this record only because TPA is HOME_TZ, and it gates path (a): no renderer line is written until r24 is attributed against case (b), fixture (f) exists (TPA shape plus a non-home twin), and rule 11 passes all fixtures and the 22-morning corpus 66/66 with zones identical.

### Ruled 2026-09-26 (second session) — r24 fix is shape (B); 9/24 ruling amended

Ruled (Nick, 2026-09-26, on Claude's recommendation): r24's fix is a code-side IATA → IANA
table (`airports.py`) applied as a post-parse override, replacing the zone field on any
endpoint named only by an airport code, null or not. The override runs after parse and
before `validate()` (ruled 2026-09-27), so a table zone passes the same `ZoneInfo`
admission as a model zone. Discriminator for "codes only" is
exact (byte comparison), ruled at build time against the actual record fields. A
`SYNTHESIS ZONE` tripwire fires on any code not in the table.

Amendment: this amends the 2026-09-24 ruling that zone-of-render is decided on the
synthesis side. Synthesis still decides every endpoint except codes; codes are a table.

Fix (A) — a forced per-group basis field in the reply schema — is parked behind a trigger
(the first live or replayed non-code endpoint whose zone wobbles across passes), not built.

Rule-11 tightening is void: three input variables (snippet, code choice, group size) were
each varied against the 09-11 morning; none moved the null. No prompt change.

MAX_TOKENS stays 4000. A long-mode reply hitting the cap is an accepted wart with
`stop_reason` on the `SYNTHESIS FALLBACK` line as the tripwire; its content is unobserved.

Gate unchanged: path (a) waits on this build — fixtures 7/7 on three byte-identical passes
(f1 TPA → DFW, f2 LAX → DFW added), corpus 66/66 zones identical, 0 short-mode fallbacks.

### Ruled 2026-09-27 (second session) — label vs convert on path (a)

Applies to the path (a) line: a merged primary rendered in the group's `render_zone` / `end_render_zone`.

- **Discriminator is stored-zone presence, byte-level:** `stored zone is None` vs not. No scoring, no inference from summary text.
- **Zone-less record (gmail-only, no zone captured) → label.** The stored clock is the venue's wall-clock; render it unchanged and attach the render zone's abbreviation (`18:30` + `America/Los_Angeles` → `6:30 PM PDT`). Converting would assert the clock meant `HOME_TZ` and invent a full-offset error.
- **Zoned record (Calendar, or gmail with a captured zone) → convert.** The instant is correct; shift it into the render zone. Only the display zone changes. Each endpoint converts into its own render zone (AA 3112: `2:24 PM EDT – 4:13 PM CDT`).
- **Fallback unchanged (9/24 sub-ruling):** null or invalid render zone → today's stored-zone render, no mark.
- **Display-only:** nothing into `_distinct_times`, `derive_conflicts`, identity, state, or Reminders.
- **Accepted wart:** a zone-less clock written in the sender's home zone rather than the venue's is labeled wrongly. Unrecoverable from the data; label-only fails it identically; no heuristic added.
- Decides f2's expected line: r10 stores `08:05`, stored zone null, render zone `America/Los_Angeles` → `8:05 AM PDT` (the `18:30` above is illustration only, not the fixture). Splits the step-3 fixture expectations into the two branches.

### Ruled 2026-09-27 (second session) — seam-report rulings for the path (a) build

Rulings on the four questions and one choice the step-2 seam report surfaced. Build scope for step 3 is exactly this.

- **Data carry is (i):** `merge_coming_up` puts source data on the merged item — zoned `start`/`end` for calendar events, bare `clock` for gmail appointments. Today's frame throughout. Rebuilding from the primary record (ii) is rejected: it reads a recorded frame and gets the stand-in case wrong (item and `group["primary"]` differ when the primary is absent from items).
- **Seam:** inside `render_coming_up`, after journey expansion and before the line is formatted; applies to pairs where the group exists, is not a journey, and `g.get("render_zone")` is non-null. Otherwise the label stays as built (9/24 fallback). `.get` throughout so `preview_digest.py`'s pre-zone fixture stays byte-identical.
- **Q1 — home zone stays bare.** Both branches use `_time_label`'s existing convention: home bare, non-home labeled, cross-zone ranges force both. r24 renders `2:24 PM` bare; AA 3112 renders with `EDT`/`CDT` via the cross-zone rule. The 9/27 plan's "r24 carries `EDT`" was written without checking the convention and is superseded.
- **Q2 — tiebreak (b) applies to synthesized groups only.** On an S6 fallback there is no group; the 9/2 survivor rule in `collapse_display` (longest summary) is unchanged. The 9/24 "floor on an S6 fallback morning" wording is amended out. Backlog line: S6 zoned-survivor tiebreak — fix shape is a byte-exact "item carries a zoned `start`" test inside `collapse_display`, cheap once (i) exists.
- **Q3 — converted items keep their stored date and sort key.** A convert across local midnight can place a line under the wrong day header; accepted wart, recorded in README Known limitations. Backlog line beside UTC/timezone: re-date converted items (touches `sort_key`, day headers, the window edge).
- **Q4 — step 4 proof is `test_render_zone.py`** (amended 2026-09-27: `preview_digest.py` takes no fixture file and is not extended — ruled) on self-authored fixtures in the events-and-state shape, `fixtures/render_zone_fixtures.json`, plus a groups list carrying the zone keys: 11 cases — (a), (b) fires and its negative, (c), f1, f2, the AA 3112 and r24 shapes, null fallback, partial end key, and the Q3 midnight wart pinned as-is. No records→items converter; no replay of 09-18/09-11 at this step. Expected day header and line are pinned by script from the captured run, never typed; the dash convention is whatever `_time_label` emits. Live proof is the step-6 kickstart — proven 2026-09-27 17:32 (runs 32, exit 0, `digest.err` 0, first live `render_zone` observed on a home-zone group); the non-home branches remain proven by fixture only until a non-home morning.
- **Unreachable branch, documented:** "gmail with a captured zone → convert" cannot occur today — `build_records` passes `None` for every gmail zone (`synthesize.py:220`). The rule stands (discriminator is on data, not source); a future extractor change that captures zones lands in convert by design.
- **Loose end closed:** synthesis input is the rendered partition (`digest.py:741–746`); 46 open against an empty input is appointments outside the 7-day window or approved onto the calendar.

### Ruled 2026-09-27 (second session) — path (a) build rulings

- **No renderer-side tripwire.** An unresolvable render zone at the seam falls back to the label as built, silently. `validate()` has already nulled and logged (`SYNTHESIS ZONE`) every key `ZoneInfo` rejects on the scheduled path, so a bad key only reaches the renderer from a grouping that bypassed `validate()`. A log line there would make the renderer print unconditionally and add a second writer to the `ZONE` line `synth_dump.py` reads. Reason recorded at the fallback in `_render_zone_label`.
- **Tiebreak (b) requires the same instant.** Inside a synthesized, non-journey group: the primary is unzoned and has a clock; exactly one member carries a zoned start; that start, converted into `render_zone` when the key admits (else its stored zone), has a clock equal to the primary's, compared as time values. `clock = None` never swaps. `group["primary"]` is never written — `_fold` keeps the swapped item for display only.
- **All or nothing on a converted range.** A zoned item with an end converts only when both `render_zone` and `end_render_zone` admit; otherwise the whole label stays as built. No half-converted line mixing a model zone and a stored zone.
- **Stash at close.** A session that edits a module the 7:00 run imports ends in one of two states only: committed and kickstarted, or `git stash push -u` with the last commit clean on disk for the next scheduled run.

## Rulings 2026-10-02 (primary-drift design table)

Correction to the record (9/30 framing was wrong for one shape): "code-derived from stored
instants" protects the renderer from mislabeling a stored instant; it does nothing when two
copies of one obligation store different clocks (calendar copy 04:00 America/Los_Angeles,
gmail copy 07:00 bare home-zone clock). A primary flip onto the gmail copy puts a wrong time
on the page. Tiebreak (b) cannot rescue it (it swaps only on equal clocks). Primary drift is a
wrong-time defect, not cosmetic. Reproducible figure, current prompt @4000: 9 mornings /
13 groups over 22; old prompt 12 / 18. The 9/26 "11 / 17" figure is not reproducible.

1. Ruled (Nick, 10/02, on recommendation) -- option (b), primary selection moves into code.
   BUILT 2026-10-03: `synthesize._select_primaries` (synthesize.py:664), called from
   `synthesize()` at synthesize.py:842. Journeys for term (0) are code-derived (two or more
   distinct `FLIGHT ` identifiers, `digest._is_journey`'s test restated as
   `synthesize._is_journey`). The log line is `SYNTHESIS PRIMARY` with a JSON payload
   `{members (sorted), model, code, term}`, through `_log`, silent under `log=False`. Proof:
   `primary_fixtures.py` on `fixtures/primary_fixtures.json`; `replay_mornings.py` passes
   `PRIMARY` lines through. The one renderer touch is the comment above `_zoned_tiebreak`.
   The model keeps proposing groups and a primary; after parse, code re-selects the primary
   from the group's members by an exact ordered rule (as amended 10/03, below):
     (0) journey groups only: earliest start instant, among members that have one;
         bare-clock members sort after; ties fall through,
     (1) stored zone present, (2) timed start over all-day, (3) source == "calendar",
     (4) non-empty location, (5) shortest summary by byte length,
     (6) lowest record id as final tie.
   Byte comparisons throughout. Log `SYNTHESIS PRIMARY` (group label, model's pick, code's
   pick) only when the code's pick differs from the model's. Display-only; identity untouched;
   no prompt change; no renderer change. Rule 9 stays in the prompt as the proposal.
   Reminder titles are structurally unaffected (`write_back` titles from the commitment's own
   `what`, never `group["primary"]`).
   Placement ruled 10/03 (Nick, on the seam read): the selector runs after validate()
   returns and before the RESULT log (validate() applies canonical order; the order is
   independent of primary), on every group with two or more members.
   Reason: `validate()` checks the primary only for membership and rewrites nothing, so
   running before it buys nothing and would (a) require the selector to guard malformed
   groups and (b) silently repair a model primary absent from `members`, a failure that
   today reaches the S6 fallback and must keep doing so. The `SYNTHESIS PRIMARY` log line
   is keyed by the sorted member ids, never by group index.
   Amendments ruled 10/03 (Nick, on recommendation), from the seam read's item 7:
   - Journeys: rule 9's "earliest departure" had no term in (1)-(5); a later leg could
     become primary and move the Departure line. Term (0) added, journey groups only.
     Code states at build how a group is identified as a journey.
   - Hotels: prompt rule 7 (timed check-in over all-day stay) was reversed for a gmail
     check-in beside a calendar all-day stay (tie on (1), stay wins on source). Term (2),
     timed over all-day, added; an exact presence check on the start time. No other named
     twin shape is affected (cars, AA, Dollar are timed on both copies).
   - Tiebreak (b) is dead after the selector (an unzoned primary now means no member is
     zoned). Left in place with a comment saying so; removal belongs to backlog line 1.
   Counter on record: two selection rules now coexist (this selector; `collapse_display`'s
   9/2 survivor rule on the fallback path). Accepted for scope -- see backlog line 1.

2. Ruled (Nick, 10/02) -- option (i), confirmation codes stay on the line as rendered.
   Reopen trigger: a confirmation code reaching a reminder title (structurally impossible
   today).

3. Ruled (Nick, 10/02) -- watchdog heartbeat priority stays 0 as built. Absence is the
   signal; 0 for the Sunday "alive", 1 for the failure page keeps them distinguishable.

4. Ruled (Nick, 10/02) -- order: the (b) build is next, before the iMessage design table,
   then the Grok avatar layer (a defect on record carries its fix's place in the order).

Backlog lines added 10/02 (shape only, not ruled):
- Line 1: unify the (b) selector, `collapse_display`'s survivor rule, and the S6
  zoned-survivor line into one exact rule; rule 9 then becomes removable from the prompt.
- Line 2: gmail-captured-zone -- extractor captures a stated zone when the email body states
  one, so `digest.py`'s unreachable "gmail with a captured zone -> convert" branch comes
  alive. Honest residue: emails stating a clock with no zone.

Build proof for (b) (ruled 10/02): fixtures in `fixtures/` for each twin shape -- car pick-up
cal/gm, car drop-off cal/gm with unequal clocks, hotel a/b (confirmation suffix), AA-a/AA-b,
Dollar cal/gm -- plus negatives (single-member group: no override, no log; model's pick already
the rule's pick: no log), byte-identical over three passes; corpus three passes on the current
prompt with primary identical on all 22 mornings, zero fallbacks, membership per the 9/26
criterion; override count reported before commit. One commit, push with `ls-remote`
read-back, no kickstart. Verification is the next live morning holding a twin: one line per
obligation carrying the calendar copy's clock and location.


## Rulings 2026-10-04 (iMessage-as-source design table)

Design only; nothing built. Summary: Session_Summary_2026-10-04.md.

- **Identity (A), namespaced coexistence.** Text commitment keys are `"imsg:<guid>:<position>"`; seen-set entry `"imsg:<guid>"`. Gmail keys and entries byte-untouched; `state.json` not migrated. Native id is `message.guid` (iCloud-stable), never `ROWID` (local to one database file; dies with it and mints duplicates against an intact Reminders list). The stored key stays opaque: nothing parses it, so a second source joins by prefix alone.
- **Unit:** one message, extracted once. The conversation window is context only; an obligation attaches to the message that completes it. "Thursday?" extracts nothing; "Thursday 2 works" extracts, attached to that message. Window bounds K (messages) and T (age) are set at fixture time against real threads, not ruled at the table.
- **Scope:** allowlist of participant handles, byte-exact, in `~/.personal_assistant.env`. Gates *before* extraction — a non-allowlisted text costs zero tokens and cannot produce a line. Both directions are read within allowlisted chats. An obligation from an unlisted sender goes to Calendar via Personal Organization, and the allowlist grows by that same consultation. This is a scope boundary like Calendar-read, not a suppression; one-stop-shop holds inside it.
- **Extraction:** a sibling prompt for texts; output schema byte-identical to the mail prompt's (state, synthesis, render, Reminders consume it — it must not fork). The mail prompt is not edited. What counts: a fixed *when*, Nick a party, *this message* completes it; open questions / "sometime" / "we should" extract nothing. Relative dates resolve at extraction, never synthesis, against the message send timestamp in the prompt, by stated convention: "this X" and bare day-words = nearest upcoming X including today; "next X" = the one after. Post-extraction guard: a resolved date outside a bounded window after the message timestamp is dropped with a log line, never rendered. Zone: machine-local for now.
- **Display:** `[text]` tag on text-sourced lines only, carried in the display-only reminder title; email and calendar lines byte-unchanged.
- **Write surface unchanged.** `reminders_write.py` and `resolve.py` are not edited for texts; any need to edit them means the schema forked and the question returns to the table. Read-only boundary extends to `chat.db` by construction: (1) copy `chat.db` plus `-wal` and `-shm` sidecars to a temp dir, never connect to the live file; (2) open the copy with `mode=ro`. The `-wal` copy is what makes the snapshot complete (newest texts live there until checkpoint).
- **TCC:** Full Disk Access granted to the exact binary launchd runs, `/Library/Frameworks/Python.framework/Versions/3.14/bin/python3` — not Terminal, not a symlink. A framework upgrade (e.g. 3.15) is a new path and the grant is gone; the interpreter path is now load-bearing for two grants. Grant before the next scheduled run; proof is kickstart-while-watching with `IMESSAGE FETCH <n> messages from <k> allowlisted chats` in `digest.out`. A Terminal pass proves nothing.
- **Probe first:** `osascript` against Messages under the existing Automation grant (proven to exist by mothballed `send_imessage.py`) — does the scripting dictionary expose message history? Expected no (unverified); if no, `chat.db` is the only read surface and FDA is required.
- **Degrade loudly:** on any text-read failure, catch, log `IMESSAGE FETCH FAILED <errno>`, run the rest of the morning, and carry one line on the page stating the text source was unavailable. A source's state is a fact, not an instruction — the constitution holds. The watchdog stays silent (the morning delivers and pairs); the loudness is the digest's own line. Backlog, behind a trigger: route through the watchdog if the line clutters the page.
- **Honest limits** text for the README Known-limitations section is ruled as drafted in the 10/04 summary and lands at build.
- **Placement:** iMessage builds next. Two-plus sessions: probe → fixtures → build → grant-and-proof. No production file is edited until the probe has reported.
- **Logged, not ruled:** tree hygiene — `send_imessage.py` (mothballed pre-Pushover delivery) and `calendar_write.py` (Step 5a gated writer, imported only by `approve.py`, dead under `calendar.readonly`) — remove or document.

## Rulings 2026-10-05 (iMessage build, session one)

- Window guard drops past, unparseable, AND null dates: a text commitment with no fixed date never renders.
- Raw handles never reach the prompt. Senders are labeled `me`, `them`, `them-2`… by first appearance in the window; no `is_group` argument.
- `what` names the activity only — never restating the date or time.
- A statement by `me` of what I will do by a day is a complete commitment on its own (a task) and needs no reply.
- A reply that affirms a proposal completes the arrangement, however it is worded — "yes", "sure", "ok", "that works" are illustrations, not a list. An affirmation needs no time of its own; if the proposal carried a date, the affirming reply completes it. (Measured 10/05: the model treated "sure" without a time as not completing; fix is this principle in the prompt, not a word list.)
- Fixture i is graded on the extraction, before the guard (the 10/05 post-guard grading was withdrawn the same evening). It FAILs on record until the affirmation fix passes it; the guard drop is then exercised live.
- Discriminators i2–i5 (3/3 each): i2 day-of-month/no time/"sure" FAIL; i3 day-of-month/7pm/"sure" PASS; i4 day-word/no time/"sure" FAIL; i5 day-of-month/no time/"yes" PASS. Single-variable on i5: "sure" with no time attached is the cause — not the date form, not the distance; "sure" with a time (i3) completes.
- Harness criterion: exact on `type`/`date`/`time`/`action_needed`; `what` non-empty and printed, not graded. Commit criterion: every graded line PASS on three separate `--live` runs.
- `extract_imessage.py` is a sibling module; nothing on the 07:00 path imports it until session two wiring.

## Rulings 2026-10-06 (iMessage build, session two)

- Affirmation principle in the prompt (10/05 ruling) landed: i2 and i4 PASS ×3, no regression. 48 calls.
- i7/i8 discriminators (both PASS ×3): year rollover alone (26 d) and long distance alone (156 d, same year) both extract. i8 gave the first live `IMESSAGE GUARD` drop. What remains on i is next-year-and-far, 157–253 d band — outside the guard either way.
- i9 (next-year edge inside the window, 119 d) PASS ×3. The guard window is covered end to end across a year boundary.
- Ruled: fixture i:1 is a documented-limit probe, not a graded case. New fixture key `"ungraded": [targets]`; live layer prints `UNGRADED`, counts toward neither exit code nor PASS total; offline layer unchanged. Reopen trigger: guard window re-set above ~150 d.
- Recorded, not fixed tonight: d:1's what restated "Thursday at 3" on 3 of 6 runs — a lapse against the ruled "activity only" rule. what titles every reminder (reminders_write.py:303) and leads every digest line (digest.py:248), and the text path inherits both at wiring, so this is graded before the wiring commit, not after. Candidate mechanism (unruled): a negative grade on what — no digits, weekday names, month names, or am/pm tokens — exact and bounded, since free-text exact match is not. First item of session three.
- Commit evidence: three separate `--live` runs, 23/23 graded, 1 ungraded, exit 0, identical PASS line md5. Evidence: `~/Documents/Claude/personal_assistant_scratch/2026-10-06/live7_1–3.txt`.
- i6 ("yes" + day-word + no time) reserved, unruled.

## Rulings 2026-10-08 (iMessage build: decoder, ground truth, what-grade, K/T)

- Ruled: message bodies are decoded from `message.attributedBody` (typedstream) by a minimal hand-written parser in the repo — no third-party dependency, no Swift/ObjC shell-out. The parser must handle the variable-width NSString length prefix. On any decode failure: `body=None`, one `digest.out` line carrying the guid, message skipped, morning delivers. Never a partial string.
- Ruled: decoder ground truth is the set of recent rows holding both `text` and `attributedBody` (~172 on 10/07). The harness prints three counts only — matched / mismatched / skipped — and no bodies. Every skip must be explained by a nameable row type (attachment-only, tapback, empty). Any mismatch on a row with real text is a fail. Passing this gates wiring. Harness fixtures are self-authored blobs, never real ones.
- Ruled: the `what` negative grade is a byte-level token check — `what` fails if it contains any digit, weekday name, month name, or am/pm token. Three runs, all graded `what` fields pass. One tightening sentence allowed, then three more runs; a second failure stops wiring. Runs before the wiring commit.
- Ruled (provisional): K = 6 prior messages of chat context; T = 48-hour fetch window. Re-set after the first live mornings if reply pairs are being cut.
- Schedule: 10/08 what-grade; 10/09 decoder committed proven-but-unwired; 10/10 fetch + wiring + kickstart-while-watching; 10/11 slack. Hard stop 10/13 (trip).

## Rulings 2026-10-09 (what-gate, clock-hour)

- Ruled: `apply_what_gate(parsed)` in `extract_imessage.py`, sibling of `apply_window_guard`, same print-to-stdout style. Any `what` failing `what_clean` has every banned word stripped whole — a whitespace-delimited word holding a digit goes entire (`3pm`, `15:00`, `3rd`), as does one holding a weekday, month or am/pm token — together with the joiner immediately before it (`on`, `at`, `in`, `this`, `next`, `for`); whitespace collapsed, leading/trailing punctuation trimmed, first character capitalized, one `WHAT SANITIZED before=<repr> after=<repr>` line. A title left empty drops its item with `WHAT SANITIZED dropped before=<repr>`. Non-dict items, and dicts whose `what` is not a string, pass through unchanged.
- Ruled: order is gate → guard, in the harness and in production. `imessage_fixtures.py` runs gate, then guard, then grades both the gated list and the after-guard list, so the grade sees the post-gate title.
- Ruled: one token list. `WHAT_BANNED` and `what_clean` live in `extract_imessage.py`; the harness imports `what_clean` and `apply_what_gate` and keeps no copy.
- Accepted wart: "Lunch at 3 Oaks" → "Lunch Oaks" (the digit word and its joiner go; the rest of the name stays). Its record is the two-spelling pair in the harness's offline `GATE_CASES`: "Lunch at 3 Oaks" → "Lunch Oaks" with a sanitized line, "Lunch at Three Oaks" unchanged with no line.
- Ruled: clock-hour convention, one prompt edit appended to the `"time"` field rule — a bare hour with no am/pm reads as PM for 1–7, AM for 8–11, noon for 12, unless the activity makes the other reading obvious ("breakfast at 7" is 07:00, "dinner at 8" is 20:00); unresolvable → `"time"` null, never a guess. The 10/08 tightening sentence is not reapplied. Fixtures l (dinner at 8 → 20:00), m (coffee at 9 → 09:00), n (call at 6 → 18:00), o (breakfast at 7 → 07:00); graded live count is 27 (23 cases, 1 ungraded).
- This section supersedes the 10/08 what-grade bullet, which is left as written: the grade stays, and a failing title is now sanitized by the gate before grading rather than stopping wiring.
