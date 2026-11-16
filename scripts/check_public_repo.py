"""Fail closed on common client data and credential leaks in the Git index.

Run after `git add`, before a first push. This is a guard, not a guarantee.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCKED_PARTS = {"data", "raw", "exports", "uploads", "node_modules", ".venv", "__pycache__"}
BLOCKED_SUFFIXES = {".sqlite", ".sqlite3", ".db", ".xlsx", ".xls", ".csv", ".tsv", ".pdf", ".pem", ".key", ".p12"}
PATTERNS = {
    "client name": re.compile(rb"\x6c\x65\x65\x6c\x6f\x6e\x67\x73", re.I),
    "local user path": re.compile(rb"(?:[A-Z]:\x5cUsers\x5c|\x2fUsers\x2f)[^\s/\\]+", re.I),
    "private network address": re.compile(rb"192\.168\.0\.200"),
    "credential assignment": re.compile(rb"(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{20,}", re.I),
}


def main():
    result = subprocess.run(["git", "ls-files", "--cached", "-z"], cwd=ROOT, capture_output=True, check=True)
    names = [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]
    if not names:
        print("No staged files. Run git add before the publication check.")
        return 1
    failures = []
    for name in names:
        path = Path(name)
        if BLOCKED_PARTS & set(path.parts) or path.suffix.lower() in BLOCKED_SUFFIXES:
            failures.append((name, "blocked data path or extension"))
            continue
        blob = (ROOT / path).read_bytes()
        if len(blob) > 2_000_000:
            failures.append((name, "file larger than 2 MB"))
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(blob):
                failures.append((name, label))
    if failures:
        for name, reason in failures:
            print(f"BLOCKED: {name} ({reason})")
        return 1
    print(f"Publication check passed: {len(names)} staged source files; no blocked client data detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
