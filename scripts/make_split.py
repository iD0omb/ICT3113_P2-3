"""Assignment 2, Step 1: split the frozen golden set into a tuning set and a test set.

  python scripts/make_split.py      # writes a2/golden_split.csv, or verifies it if it already exists

Rule (A2 brief, Step 1): sort the golden-set tickets by row number (ticket_number, the row in the
team's data); odd rows are the tuning set, even rows the test set. "Odd/even rows" is read as the
position in the sorted list (1st, 3rd, 5th, ... -> tuning; 2nd, 4th, 6th, ... -> test): sorting first
would be unnecessary if it meant the parity of the ticket number itself.

The escalation rule is designed and tuned on tuning rows only; every reported accuracy uses test rows.

Standard library only. Reads Golden_Test_Set.csv (the labels frozen in Assignment 1 and used by every
A1 accuracy run) without modifying it. Never overwrites an existing split: if the output file exists,
the split is regenerated in memory and compared with it.
"""
import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from accuracy_run import CATEGORIES, REPO, canonical, check, read_golden  # noqa: E402

FIELDS = ["position", "ticket_number", "label", "set"]
SMALL = 10  # A2 brief, "Small numbers": flag test categories this small for the per-category limit


def make_split(golden):
    ordered = sorted(golden, key=lambda g: int(g["ticket_number"]))
    return [
        {"position": str(i), "ticket_number": g["ticket_number"], "label": canonical(g["label"]),
         "set": "tuning" if i % 2 else "test"}
        for i, g in enumerate(ordered, start=1)
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golden", type=Path, default=REPO / "Golden_Test_Set.csv")
    parser.add_argument("--out", type=Path, default=REPO / "a2" / "golden_split.csv")
    args = parser.parse_args()

    golden = read_golden(args.golden)
    if check(golden):
        sys.exit("aborting: fix the golden set problems above first")
    rows = make_split(golden)

    if args.out.exists():
        with open(args.out, encoding="utf-8", newline="") as f:
            existing = list(csv.DictReader(f))
        if existing != rows:
            sys.exit(f"{args.out} differs from the split this rule produces; left unchanged")
        print(f"\n{args.out} matches the rule (left unchanged)")
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nwrote {args.out}")

    counts = {s: Counter(r["label"] for r in rows if r["set"] == s) for s in ("tuning", "test")}
    print(f"\n{'category':28s} {'tuning':>6s} {'test':>5s}")
    for c in CATEGORIES:
        n = counts["test"][c]
        note = f"  small test category: 1 ticket = {100 / n:.1f} pts" if 0 < n < SMALL else ""
        print(f"{c:28s} {counts['tuning'][c]:6d} {n:5d}{note}")
    print(f"{'total':28s} {sum(counts['tuning'].values()):6d} {sum(counts['test'].values()):5d}")


if __name__ == "__main__":
    main()
