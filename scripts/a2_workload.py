"""Assignment 2, Step 3: the fixed speed-test workload, warm-up tickets and S-measurement tickets.

  python scripts/a2_workload.py       # writes the files, or verifies them if they already exist

Pool: team rows (ict3113_tickets.csv) outside the golden set, excluding tickets already sent in A2
(memory check 3000-3002, smoke test 3989-3999). From the pool:

  Warm-up (3): rows 3992, 3993, 3996. In the smoke test qwen3:4b predicted Debt collection for all three,
    which escalates in both settings A and B, so warm-up loads both cascade models. Deterministic
    (temperature 0, fixed seed). The same three are used before every run of every configuration.
  S measurement (3): the three remaining pool tickets closest to the pool's median length ("typical").
  Workload (120): the remaining pool in a fixed random order (seed 3113), first 120. A run uses as many as
    its schedule sends, always from the top of the list; 120 covers S down to 1.25 s.

Writes a2/speed_workload.csv (order, row, chars), a2/speed_extra_tickets.csv (warm-up and S rows) and
a2/speed_narratives.txt (one narrative per line, whitespace collapsed, for JMeter's CSV Data Set).
Never overwrites: existing files are compared with what the rule produces.
"""
import csv
import random
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from accuracy_run import REPO, read_golden  # noqa: E402

SEED, N_WORKLOAD = 3113, 120
WARMUP = ["3992", "3993", "3996"]
ALREADY_SENT = {"3000", "3001", "3002", "3989", "3992", "3993", "3994", "3995", "3996", "3997", "3999"}
OUT = REPO / "a2"


def one_line(text):
    return re.sub(r"\s+", " ", text).strip()


def pct(values, p):
    s = sorted(values)
    return s[min(len(s) - 1, int(p * len(s)))]


def build():
    golden = {g["ticket_number"] for g in read_golden(REPO / "Golden_Test_Set.csv")}
    with open(REPO / "ict3113_tickets.csv", encoding="utf-8", newline="") as f:
        team = {r["row"]: one_line(r["narrative"]) for r in csv.DictReader(f) if one_line(r["narrative"])}
    pool = sorted((r for r in team if r not in golden and r not in ALREADY_SENT), key=int)
    median = statistics.median(len(team[r]) for r in pool)
    s_rows = sorted(sorted(pool, key=lambda r: (abs(len(team[r]) - median), int(r)))[:3], key=int)
    rest = [r for r in pool if r not in s_rows]
    random.Random(SEED).shuffle(rest)
    workload = rest[:N_WORKLOAD]
    files = {
        "speed_workload.csv": [["order", "row", "chars"]] + [[str(i), r, str(len(team[r]))]
                                                            for i, r in enumerate(workload, 1)],
        "speed_extra_tickets.csv": [["role", "row", "chars"]]
        + [["warmup", r, str(len(team[r]))] for r in WARMUP] + [["s_measure", r, str(len(team[r]))] for r in s_rows],
    }
    narratives = "\n".join(team[r] for r in workload) + "\n"
    return team, golden, pool, workload, s_rows, files, narratives


def main():
    team, golden, pool, workload, s_rows, files, narratives = build()
    for name, rows in files.items():
        path = OUT / name
        text = "".join(",".join(r) + "\n" for r in rows)
        if path.exists():
            if path.read_text(encoding="utf-8") != text:
                sys.exit(f"{path} differs from what this rule produces; left unchanged")
        else:
            path.write_text(text, encoding="utf-8", newline="")
    path = OUT / "speed_narratives.txt"
    if path.exists():
        if path.read_text(encoding="utf-8") != narratives:
            sys.exit(f"{path} differs from what this rule produces; left unchanged")
    else:
        path.write_text(narratives, encoding="utf-8", newline="")

    overlap = set(workload) & (golden | set(s_rows) | set(WARMUP))
    if overlap:
        sys.exit(f"workload overlaps golden/warm-up/S tickets: {sorted(overlap)}")
    lens_team = [len(t) for t in team.values()]
    lens_w = [len(team[r]) for r in workload]
    long_cut = pct(lens_team, 0.90)
    print(f"team rows {len(team)}, golden {len(golden)}, pool {len(pool)}; workload {len(workload)}, "
          f"warm-up {WARMUP}, S {s_rows} ({[len(team[r]) for r in s_rows]} chars)")
    print(f"{'chars':16s} {'p10':>5s} {'p50':>5s} {'p90':>5s} {'max':>5s}")
    for name, lens in (("all team rows", lens_team), ("workload (120)", lens_w), ("first 60", lens_w[:60])):
        print(f"{name:16s} {pct(lens, .1):5d} {pct(lens, .5):5d} {pct(lens, .9):5d} {max(lens):5d}")
    print(f"long tickets (>= team p90, {long_cut} chars): {sum(l >= long_cut for l in lens_w)} of 120, "
          f"{sum(l >= long_cut for l in lens_w[:60])} of the first 60")
    print(f"-> {OUT / 'speed_workload.csv'}, {OUT / 'speed_extra_tickets.csv'}, {OUT / 'speed_narratives.txt'}")


if __name__ == "__main__":
    main()
