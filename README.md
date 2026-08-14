# Personal Assistant

**A daily-digest system that reports obligations which already exist — and never generates its own.**

Every morning at 7:00, it reads Gmail and Google Calendar, extracts commitments with the Claude API, reconciles them against what's already been resolved, and delivers a three-section digest as a Pushover notification:

- **NEEDS ATTENTION** — dated tasks now overdue
- **TO DO** — dateless tasks (shown daily until resolved) and dated tasks within 7 days
- **COMING UP** — calendar events and extracted appointments, merged chronologically, 7-day horizon

Open action items are also written to a dedicated Apple Reminders list. Ticking an item on the phone resolves it — the next morning's run reads the completion and the item vanishes from the digest. No terminal, no laptop, no app beyond what's already on the phone.

It runs unattended in production via launchd on macOS. Typical run: fires within minutes of 7:00, delivers in under 90 seconds.

<!-- screenshot slot: scrubbed Pushover digest goes here -->

*Known limitations are documented [below](#known-limitations) — including the ones found on real mornings.*

---

## Design decisions

### The product had to be discovered, not specified

The original design generated proposals: it drafted calendar events from extracted commitments and asked for approval each morning. Every layer worked. The output was unusable — the user had become the assistant's clerk, approving work the assistant had invented. One screenshot ended it: a Delta flight the assistant proposed writing was already in the calendar, written by Google's travel parser, with an end time (`7:40 PM`) that existed nowhere in the assistant's pipeline — Google had parsed the full flight range while the assistant defaulted to start + 1 hour. For the highest-volume mail category, the system was paying to extract, proposing a duplicate, and demanding approval for something already handled better.

The write path was mothballed the same week, and the product statement was rewritten to what the system actually is: **it reports obligations that already exist; it never generates its own.** A corollary that shapes every rendered line: no imperatives in the deliverable. The digest states what exists; it never instructs.

### Read-only, with one sanctioned exception

The assistant reads Gmail and Calendar and writes to neither. The single write surface is a dedicated Apple Reminders list — and *dedicated* is load-bearing. Writing an extracted commitment there is rendering an existing obligation to a second surface, not creating one; and a contained namespace's worst failure is a junk list you delete, unlike schedule data the assistant doesn't own.

### Identity is exact, and fuzzy matching is banned

A commitment's identity is `(gmail message id, array position)`. Resolution happens only when `completed = true` appears on the exact stored reminder id — titles are display-only, deletion means nothing, and no similarity matching touches state, ever. The reasoning: over-matching silently deletes real commitments; under-matching makes noise. Noise is survivable; silent data loss is not. The ban was stress-tested when a title collision put a completion on the wrong item — id-based resolution absorbed it.

### Completeness beats brevity

The digest never suppresses an obligation because another system also tracks it. When Google's parser and the assistant's extraction both see one hotel booking, both render — the display is honest about what two systems saw. Deduplication (planned, below) will collapse the *display*; it will never drop data.

---

## Architecture

One linear pass, once per morning:

```
fetch (Gmail + Calendar) → extract (Claude API) → save
    → reconcile (read Reminders completions, resolve by exact id)
    → partition (classify each open commitment — called exactly once)
    → write-back (create reminders for new open action items)
    → render (full + split) → deliver (Pushover)
```

Two structural guarantees fall out of the shape. Classification lives in a single function called exactly once per run, and both renderers consume its output — so the digest and the Reminders list cannot disagree, by construction. And reconciliation runs before partition in the same pass, so a ticked item can never render as open.

Calendar data is display-only: it flows to the renderer and never enters state. Extracted appointments auto-drop once their date passes — the date passing *is* the resolution — while dated tasks go overdue and persist. Deletion of a reminder means nothing; only completion on the exact stored id resolves.

### File map

| Tier | Files | |
|---|---|---|
| **Pipeline** | `deliver.py` | entry point; launchd runs this |
| | `digest.py` | orchestration, partition, both renderers |
| | `fetch_gmail.py` / `fetch_calendar.py` | source reads |
| | `extract.py` | Claude API extraction |
| | `state.py` | persistence, nothing else |
| | `resolve.py` | resolution + manual-resolution fallback picker |
| | `reminders_write.py` | the one sanctioned write surface |
| | `send_push.py` | Pushover delivery, part-splitting |
| | `env_loader.py` | credential self-provisioning (launchd inherits almost no environment) |
| **Test / fixture** | `preview_digest.py` | renders all fixtures for $0 — the primary test harness |
| | `test_plan_parts.py` | part-splitting cases |
| | `test_reminders_pipeline.py` | reminders pipeline tests |
| | `fixtures/` | captured duplication cluster + rendered cross-source lines |
| **Diagnostic** | `probe_calendar.py` | calendar data-surface probe; retained because the timezone work (below) starts with it |
| **Mothballed** | `calendar_write.py`, `approve.py` | the killed write path — retained unwired; its decision records still feed a rendering rule, and the design reasoning is banked for any future write feature |
| | `send_imessage.py` | retired delivery path |

Mothballed files are kept deliberately: they carry the reasoning that killed them, and deleting them would delete the evidence.

---

## Setup

Documented from the running install. Four pieces:

**Credentials.** Three variables in `~/.personal_assistant.env` (chmod 600, never committed): `ANTHROPIC_API_KEY`, `PUSHOVER_USER_KEY`, `PUSHOVER_APP_TOKEN`. Every script self-provisions through `env_loader.py` — necessary because launchd's inherited environment is nearly empty; a script that works from a shell and assumes its environment will silently fail at 7am. Google OAuth lives separately as `client_secret_*.json` + `token.json` in the repo directory, both gitignored. The OAuth app must be published to Production in Google Cloud Console, or refresh tokens expire weekly.

**Schedule.** A LaunchAgent (`com.nickrusso.dailydigest.plist`, tracked at repo root, installed by copy to `~/Library/LaunchAgents/`) fires `deliver.py` at 07:00 with stdout/stderr appended to `~/Library/Logs/personal_assistant/`. A `pmset` wake at 6:55 ensures the machine is up to hear it.

**Permissions — the part that bites.** macOS Automation grants are per-app, and the Python that launchd runs is not the Python your terminal runs. A grant to Terminal proves nothing about the scheduled path. The framework interpreter needs its own Reminders grant, and the only honest proof is firing the real scheduled path on demand:

```
launchctl kickstart -p gui/$UID/com.nickrusso.dailydigest
```

— watched live with `tail -f` on the log. Green in a shell is not green at 7am.

**Consent sequencing.** Any OAuth scope change requires interactive re-consent *before* the next scheduled run — otherwise the 7am job blocks on a consent prompt nobody is watching.

## Operations

The morning health check is a glance, not a ritual:

- Notification arrived (typical start 07:00–07:24; launchd's delay varies)
- `digest.err` line count unchanged — the file only grows on failure
- `launchctl print` shows `last exit code = 0`

Each run brackets itself with `RUN START` / `PRE-SEND` timestamps in the log: delay and duration fall out by subtraction. Baseline duration 26–47s; each reminder create adds 17–45s — external-service latency is a distribution, not a number, and the timeouts are budgeted against measured bands, not guesses.

**Failure behavior is loud-stop.** The system raises and dies rather than degrading; state is written only after a full successful fetch-extract pass, so a run killed at the first network call leaves state untouched. Production incident on record: four hotel mornings died at OAuth token refresh when captive-portal wifi leases expired overnight — diagnosed from the logs alone, state verified untouched by mtime, no code change required. The failure class doesn't exist on the home network.

## Verification approach

- **Fixtures over chance mail.** Tests run against self-authored fixture data through `preview_digest.py` — repeatable, free, and never dependent on what happened to arrive in the inbox. Tests never touch live state.
- **Every delete claims proof.** A deletion isn't done until a read-back confirms the end state — a void call's success and its swallowed error are indistinguishable without one.
- **Tripwires where nothing should write.** Where a code path must not write, tests monkeypatch the writers to raise — absence of evidence is not evidence of absence.
- **The scheduled path is proven as itself.** `launchctl kickstart` fires the identical launchd-context run on demand; a passing shell run proves only the shell.

## Known limitations

Found on real mornings, documented plainly.

**One obligation can render several times.** Message-level ingestion means a thread of near-identical emails (booking, confirmation, reminder) produces near-identical extractions — a live hotel booking rendered three times on consecutive days. Cross-source duplication compounds it: Google's travel parser and this system's extraction both see the same booking. The fix is designed and its fixtures are captured from the live occurrence (`fixtures/`): a display-layer collapse with an exact discriminator — same date, same time, one summary a substring of the other — chosen over fuzzy matching, which stays banned even here. State keeps every record; only the display collapses.

**In-progress multi-day events wear a stale date.** A hotel stay spanning a week renders with its start date every morning of the stay — current information under a yesterday label. Diagnosed to a rendering rule (the fetch is correct); the fix is a "through *end-date*" form for in-progress all-day events.

**Timezone rendering is inferred, not verified.** Events appear to render in their stored zone rather than the viewer's — a wrong fact, not a formatting wart, for any out-of-zone event. Unconfirmed on two data points; the diagnostic starts with `probe_calendar.py`.

**State grows without bound.** Resolved and auto-dropped records persist forever. Harmless at current volume, unaddressed by design priority, not by oversight.

## Roadmap

Sequenced by reasoning, each behind its gate:

- **Duplication display-collapse** — designed, fixtures captured; the discriminator's case-sensitivity gets ruled against the fixtures, not in advance
- **iMessage ingestion** — gated behind a privacy ruling: a contact allowlist decision before any message content reaches the extraction API
- **Additional sources** (Google Tasks, manual entry) — blocked on a source-labeling convention for multi-source item lines
- **Operational hardening** — state retention, log rotation
