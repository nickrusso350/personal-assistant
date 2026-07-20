import os
from pathlib import Path


def load_env_file():
    """Load ~/.personal_assistant.env into the environment, dotenv-style.

    Silently returns if the file is absent. Skips blank lines and comment lines
    (first non-whitespace char is '#'). Each remaining line is split on the
    first '=' only, with key and value whitespace-stripped; lines without an '='
    are skipped. A value that is at least two characters long and begins and ends
    with the same quote character (either " or ') has those outer quotes removed.

    Pairs are set with os.environ.setdefault, so existing environment variables
    always win. Never prints keys or values."""
    env_path = Path.home() / ".personal_assistant.env"
    if not env_path.exists():
        return

    with env_path.open() as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("\"", "'"):
                value = value[1:-1]
            os.environ.setdefault(key, value)
