"""Filesystem paths shared by the 7:00 digest and its watchdog (ruled 2026-10-31).

Lives apart from both so the monitor and the monitored share no import-time
point of failure: digest.py and watchdog.py each import this module, never
each other.
"""
from pathlib import Path

# The one definition in code. The other copy of the value is the
# com.nickrusso.dailydigest plist's StandardOutPath; the two are not tied.
LOG_PATH = Path.home() / "Library" / "Logs" / "personal_assistant" / "digest.out"
