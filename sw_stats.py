"""speakwrite dictation stats readout.

SpeakWrite appends one JSON object per dictation session to
~/.config/speakwrite/metrics.jsonl (JSON Lines — one object per line). This
reads that file and prints an aligned summary of dictation pace across three
windows shown together — 7-day, last-50, and all-time — plus session totals,
best wpm, total words, and the share of mic time that was excluded silence
("thinking %").

Python 3 standard library only.

Run:  .venv/bin/python sw_stats.py
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

METRICS = Path.home() / ".config" / "speakwrite" / "metrics.jsonl"


def load_sessions(path):
    """Parse the JSONL file into a list of session dicts, skipping junk.

    Tolerates a missing file, blank lines, and a malformed/partial last line.
    """
    if not path.exists():
        return []
    sessions = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict):
            sessions.append(obj)
    return sessions


def parse_date(s):
    """Parse an ISO8601 UTC date (Z suffix) into an aware datetime, or None."""
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def avg_wpm(sessions):
    """Mean of the per-session wpm values; None if there are none."""
    wpms = [s["wpm"] for s in sessions if isinstance(s.get("wpm"), (int, float))]
    if not wpms:
        return None
    return sum(wpms) / len(wpms)


def fmt_wpm(v):
    return f"{round(v)} wpm" if v is not None else "—"


def main():
    sessions = load_sessions(METRICS)
    if not sessions:
        print("No sessions recorded yet — dictate something first.")
        return

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)

    # Sort newest-first so "last 50" is the 50 most recent sessions. Sessions
    # with an unparseable date sort to the end (treated as oldest).
    def keydate(s):
        d = parse_date(s.get("date"))
        return d or datetime.min.replace(tzinfo=timezone.utc)

    by_recent = sorted(sessions, key=keydate, reverse=True)

    last7 = [s for s in sessions if (parse_date(s.get("date")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
    last50 = by_recent[:50]

    total_speaking = sum(s["speakingSeconds"] for s in sessions if isinstance(s.get("speakingSeconds"), (int, float)))
    total_mic = sum(s["totalSeconds"] for s in sessions if isinstance(s.get("totalSeconds"), (int, float)))
    thinking = (1 - total_speaking / total_mic) if total_mic else 0.0

    total_words = sum(s["words"] for s in sessions if isinstance(s.get("words"), (int, float)))
    wpms = [s["wpm"] for s in sessions if isinstance(s.get("wpm"), (int, float))]
    best = max(wpms, default=None)
    worst = min(wpms, default=None)

    print("SpeakWrite stats")
    print("================")
    print(f"  7-day avg     : {fmt_wpm(avg_wpm(last7))}  ({len(last7)} sessions)")
    print(f"  last-50 avg   : {fmt_wpm(avg_wpm(last50))}  ({len(last50)} sessions)")
    print(f"  all-time avg  : {fmt_wpm(avg_wpm(sessions))}  ({len(sessions)} sessions)")
    print()
    print(f"  total sessions: {len(sessions)}")
    print(f"  best wpm      : {fmt_wpm(best)}")
    print(f"  worst wpm     : {fmt_wpm(worst)}")
    print(f"  total words   : {total_words}")
    print(f"  thinking %    : {thinking * 100:.0f}%  (share of mic time excluded as silence/thinking)")


if __name__ == "__main__":
    main()
