"""Assignment 2: save one run's service-log lines into a new file, picked by run ID.

  python scripts/a2_save_log.py --run-id cascade-A-acc --out a2/results/accuracy/cascade-A/requests.log
  python scripts/a2_save_log.py --run-id llama-r1 --run-id llama-r1-warmup --out a2/results/speed/llama/run1/requests.log

Run on the system-under-test laptop, from the repository root, after the run has finished. Reads
logs/requests.log (never modified) and writes only the lines whose run_id is one of the given IDs, in
their original order and unchanged. Refuses to overwrite an existing file or to write an empty one.

Standard library only.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", action="append", required=True, help="run ID to keep; repeat for several")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--log", type=Path, default=REPO / "logs" / "requests.log")
    args = parser.parse_args()

    if args.out.exists():
        sys.exit(f"{args.out} already exists; refusing to overwrite saved evidence")
    wanted = set(args.run_id)
    kept, unparseable = [], 0
    with open(args.log, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                unparseable += 1
                continue
            if entry.get("run_id") in wanted:
                kept.append((line if line.endswith("\n") else line + "\n", entry))
    if not kept:
        sys.exit(f"no lines in {args.log} with run ID {sorted(wanted)}; nothing written")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        f.writelines(line for line, _ in kept)

    print(f"{len(kept)} lines -> {args.out}")
    for run_id in args.run_id:
        mine = [e for _, e in kept if e.get("run_id") == run_id]
        paths = Counter(f"{e.get('path')} {e.get('status')}" for e in mine)
        tickets = [e for e in mine if e.get("path") == "/tickets"]
        line = f"  {run_id}: {len(mine)} lines ({', '.join(f'{k}: {v}' for k, v in sorted(paths.items())) or 'none'})"
        if any("escalated" in e for e in tickets):
            line += f"; escalated {sum(bool(e.get('escalated')) for e in tickets)}/{len(tickets)}"
        print(line)
    if unparseable:
        print(f"  note: {unparseable} unparseable lines in {args.log} were skipped")


if __name__ == "__main__":
    main()
