"""Durable history snapshot: the ledger's restore file.

The watcher's ledger lives in memory and resets on every Render
deploy, and Render's free-tier disk is ephemeral per deploy, so no
bot-side file could survive an update on its own. The explainer
sidecar keeps the permanent record (an append-only archive on the
assistant's persistent home) and refreshes THIS file in the repo
from it; the watcher loads it at startup so History survives
updates. The snapshot holds the latest 100 records (the ledger's
cap), oldest first, in the raw record shape — display fields and
archive metadata stripped, no secrets of any kind.
"""
import json
import os

SNAPSHOT_FILENAME = "history_snapshot.json"
LEDGER_CAP = 100
_OUTCOMES = ("open", "tp", "sl")
_TIERS = ("setup", "confirmed", "ema_retest")
# Keys the archive adds that are not part of a ledger record.
_STRIP_KEYS = ("archived_at", "source", "reconstructed",
               "time_eat", "date_eat", "when_eat", "setup_time_eat")


def default_path():
    """Where the snapshot lives: next to this module, in the repo."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        SNAPSHOT_FILENAME)


def _valid_record(rec):
    """A dict the watcher can hold in its ledger as-is."""
    if not isinstance(rec, dict):
        return False
    if not isinstance(rec.get("epoch"), int):
        return False
    if rec.get("tier") not in _TIERS:
        return False
    if rec.get("outcome") not in _OUTCOMES:
        return False
    if rec.get("side") not in ("SELL", "BUY"):
        return False
    for key in ("entry", "sl", "tp"):
        if not isinstance(rec.get(key), (int, float)):
            return False
    return True


def load_snapshot(path):
    """Read a snapshot file. Missing, corrupt, or wrong-shaped files
    yield an empty list — startup must never fail on a bad snapshot."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    recs = [r for r in data if _valid_record(r)]
    recs.sort(key=lambda r: r["epoch"])
    return recs[-LEDGER_CAP:]


def build_snapshot(archive_path, cap=LEDGER_CAP):
    """Collapse an append-only archive to the latest state of every
    record: the last line per (tier, epoch) wins, so a record first
    archived open and later closed comes back closed. Returns the
    latest `cap` records, oldest first, in raw record shape."""
    latest = {}
    try:
        with open(archive_path, encoding="utf-8") as f:
            lines = f
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not _valid_record(rec):
                    continue
                clean = {k: v for k, v in rec.items()
                         if k not in _STRIP_KEYS}
                latest[(rec["tier"], rec["epoch"])] = clean
    except OSError:
        return []
    recs = sorted(latest.values(), key=lambda r: r["epoch"])
    return recs[-cap:]


def write_snapshot(path, records):
    """Atomic write: temp file + replace, so a crash mid-write never
    leaves a half-written snapshot for the next startup."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(records, f, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
