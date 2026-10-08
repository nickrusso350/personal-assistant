"""probe_chatdb.py — Full Disk Access proof under the scheduled path, plus a chat.db schema read.

What it does
  1. Copies ~/Library/Messages/chat.db, chat.db-wal and chat.db-shm to a dated scratch folder
     outside the repo, with timing. All three files are required: Messages keeps recent rows in
     the WAL, so a copy of chat.db alone is stale.
  2. Opens the COPY read-only (sqlite URI ?mode=ro) and prints schema, counts and date shape.
     Nothing is ever written to ~/Library/Messages.
  3. Exits 1 with the verbatim OS error if the source cannot be opened. That line is the
     verdict: PermissionError 'Operation not permitted' means the Full Disk Access grant is
     not reaching this process.

How to run it (the only run that proves anything)
  A Terminal pass proves nothing: a child of Terminal inherits Terminal's privacy grants.
  Run it under launchd with the production interpreter, as a one-shot LaunchAgent
  (label com.nickrusso.dailydigest.probe, RunAtLoad false, ProgramArguments
  [/Library/Frameworks/Python.framework/Versions/3.14/bin/python3, <this file>],
  StandardOutPath under ~/Library/Logs/personal_assistant/), bootstrap, kickstart, read the
  log, bootout, delete the plist. The plist is deliberately not tracked.

What it found (2026-10-07, macOS 26.6.2, Python 3.14.7 framework build)
  * The FDA grant must be on the RESOLVED executable. /.../3.14/bin/python3 is a symlink to
    bin/python3.14; macOS matches a path-type privacy row against the executable that actually
    opens the file, so a row keyed to the symlink path never matches, even though Settings shows
    it as allowed. Granting Resources/Python.app (org.python.python) also does nothing here:
    the launchd job never runs that binary. Grant bin/python3.14; the plist keeps bin/python3.
    Verified in the system TCC database before the passing run:
      sudo sqlite3 "/Library/Application Support/com.apple.TCC/TCC.db" \
        "select client, auth_value from access where service='kTCCServiceSystemPolicyAllFiles'"
  * macOS 26.1+ may not display Unix executables in the Full Disk Access list even when the
    grant took. The TCC query above and this probe are the checks; the list is not.
  * Message bodies are mostly NOT in message.text. In the 30 days before the run, 1,015 of
    1,187 messages had text NULL; the body lives in message.attributedBody (a typedstream
    blob). A fetcher must decode that blob.
  * message.date is nanoseconds since 2001-01-01 (Apple epoch); the decode used below is
    date/1e9 + 978307200.
  * Copy cost for a ~303 MB chat.db was 0.26 s — negligible against the 07:00 budget.

Scratch copy is sensitive
  The copy is the full message history in plain form. It lives outside the repo
  (personal_assistant_scratch/<date>/) and should be deleted once the read is done:
    rm -r ~/Documents/Claude/personal_assistant_scratch/<date>
"""
import datetime, os, shutil, sqlite3, sys, time
SRC = os.path.expanduser("~/Library/Messages")
DST = os.path.expanduser(
    "~/Documents/Claude/personal_assistant_scratch/" + (sys.argv[1] if len(sys.argv) > 1 else datetime.date.today().isoformat())
)
print("PROBE START", time.strftime("%Y-%m-%dT%H:%M:%S"), "exe=", sys.executable, "py=", sys.version.split()[0])
os.makedirs(DST, exist_ok=True)
try:
    names = [n for n in ("chat.db", "chat.db-wal", "chat.db-shm") if os.path.exists(os.path.join(SRC, n))]
    print("source files:", [(n, os.path.getsize(os.path.join(SRC, n))) for n in names])
    t0 = time.monotonic()
    for n in names:
        shutil.copy2(os.path.join(SRC, n), os.path.join(DST, n))
    print(f"copy: {time.monotonic()-t0:.2f}s")
except Exception as e:
    print("SOURCE UNREADABLE:", type(e).__name__, repr(e)); sys.exit(1)
db = sqlite3.connect(f"file:{os.path.join(DST,'chat.db')}?mode=ro", uri=True)
q = lambda s: db.execute(s).fetchall()
print("tables:", [r[0] for r in q("select name from sqlite_master where type='table' order by 1")])
for t in ("message", "handle", "chat"):
    print(f"{t} columns:", [(r[1], r[2]) for r in q(f"pragma table_info({t})")])
print("joins:", q("select sql from sqlite_master where name in ('chat_message_join','chat_handle_join')"))
print("counts:", q("select 'messages',count(*) from message union all select 'handles',count(*) from handle union all select 'chats',count(*) from chat"))
print("date shape:", q("select min(date),max(date),datetime(min(date)/1000000000+978307200,'unixepoch','localtime'),datetime(max(date)/1000000000+978307200,'unixepoch','localtime') from message"))
print("null-text share last 30d:", q("select sum(text is null),count(*) from message where date>(select max(date) from message)-30*86400*1000000000"))
print("PROBE END", time.strftime("%Y-%m-%dT%H:%M:%S"))
