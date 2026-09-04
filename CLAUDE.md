# CLAUDE.md — Personal Assistant

*Written 2026-09-04 when build sessions moved from claude.ai Chat to Claude Code. Derived from the Personal Assistant project instructions (revised 2026-08-31). The project's session summaries and design records live in `../personal_assistant_docs/` and are not tracked; read the newest summary first, and when it points backward, read the earlier ones before reconstructing.*

## What this is

A production system that reads Gmail and Google Calendar, extracts commitments via the Claude API, tracks open/resolved state across days, and delivers a digest at 7:00 AM via Pushover, with resolution from the phone via a dedicated Apple Reminders list. It runs unattended, daily, on this Mac mini via a LaunchAgent labeled `com.nickrusso.dailydigest`. **This repo is live production.** Nick rules design questions explicitly; Claude does not rule by default. Rulings are recorded in the session summaries and carry forward.

## Product statement (constitution — do not dilute)

The assistant reports obligations that already exist; it never generates its own. No imperatives anywhere in the deliverable: the digest states what exists, it never instructs. An obligation that lives in no system the assistant reads is never surfaced.

## Hard rules for Claude Code in this repo

- **Never run `digest.py`.** It is the production run: it writes `state.json`, creates Reminders, and sends Pushover. Production runs only via the LaunchAgent, or via a `launchctl kickstart` that Nick performs while watching.
- **`preview_digest.py` is the sanctioned test surface.** It is $0 and network-free by default. Do not add network or API calls to its default path.
- **Never read, edit, copy, or regenerate `state.json`.** It is a matched pair with the Reminders list via iCloud reminder ids. Fresh state against an intact list mints duplicates. It is gitignored and must stay so.
- **Never touch `token.json`, `.env`, or the client secret.** Never print their contents. Never run `probe_calendar.py` (it carries its own `SCOPES` list and overwrites `token.json`).
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
- **Synthesis (2026-09-03, S1–S6):** one Claude API call per run over the day's records returns a *grouping* — structure only, never facts. Code validates (partition, schema, one date per group, no do/happen mixing). The renderer builds every line from the primary record's real fields. When uncertain, do not merge. Synthesis never costs the morning: on failure, render the `collapse_display` fallback with a note. S2: multi-day spans render endpoints only. S3: Reminders for TO DO / ATTENTION only. S4: one reminder per obligation across sources (members share a `reminder_id`). S5: day-block register, `DDD, Mon D — <name>: depart|return`. Marker `(×N)` on synthesized lines through the 9/5 trip verification window.
- **Times:** an event renders in its own stored zone with the abbreviation when not `HOME_TZ`. The Calendar API formats `dateTime` in the calendar's zone on read; the writer's digits do not survive storage, so the reader cannot recover a wrong instant — upstream data errors are fixed upstream. Physical events display in their location's zone; a virtual-event display rule (reader's zone) is in the backlog, not built.

## Working rules

- **Read before patch.** Read the target file as it is on disk before proposing or applying a change. Read a diagnostic script before running it.
- **Every change gets a read-back** before it is called done. Every delete claim gets a read-back.
- **Matchers are built against captured stderr, never predicted.** Instruments carry literal identifiers, never discover them.
- **Measured once is not measured.** Stability of anything model-dependent means three identical results.
- **Separate "is the code right" from "is the data right."** An upstream writer's wrong instant is not a renderer defect.
- **Verification lands before the commit, or the commit is provisional.**
- **Where nothing should be written, tripwires beat absence of evidence.**
- **When a design question surfaces mid-task, stop and ask Nick.** Do not pick a default and keep going.

## Instruments

- Logs: `/Users/nickrusso_macmini/Library/Logs/personal_assistant/digest.out` and `digest.err`. Pairing `RUN START` with `PRE-SEND` in `digest.out` separates spawn-refused / crashed-mid-run / clean. `digest.err` is a watermark (currently 0), not a health check. `SYNTHESIS INPUT|RAW|RESULT|FALLBACK` lines land in `digest.out`.
- `launchctl print gui/$(id -u)/com.nickrusso.dailydigest | grep -E $'^\t(state|program|runs|last exit code) = '` — the exact-indent anchor; nested keys sit at two tabs.
- Dominant failure class: `NameResolutionError` on `oauth2.googleapis.com` at run time (network unavailable). Signature: `RUN START` with no `PRE-SEND`, exit 1.

## Quality bar

Showcase-grade: the repo should be presentable to potential employers. The reasoning is the portfolio piece, not the line count. Docstrings carry the ruling and its date. Honest limitations sections are themselves showcase material.
