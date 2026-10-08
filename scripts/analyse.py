"""Summarise every recorded run into results/summary.md (Slides 9-10) using the playbook's definitions.

  python scripts/analyse.py [--duration 20]

Reads results/<model>/accuracy/accuracy.csv, results/<model>/load/<rate>/run<n>/results.jtl and, when present,
results/<model>/requests.log (for reconciliation). Never modifies them.
"""
import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from accuracy_run import CATEGORIES, TRUIST_MIX, pct

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
LABEL_TO_PATH = {"POST /tickets": "/tickets", "GET /search": "/search", "GET /stats": "/stats"}


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def spread(values, fmt="{:.1f}"):
    """Mean of the runs with min-max, e.g. '12.3 (10.1-14.0)'."""
    if not values:
        return "-"
    mean = statistics.mean(values)
    return fmt.format(mean) if len(values) == 1 else f"{fmt.format(mean)} ({fmt.format(min(values))}–{fmt.format(max(values))})"


# --- Accuracy -----------------------------------------------------------------

def accuracy_section(models):
    out = ["## Accuracy (golden set, one ticket at a time)", ""]
    out.append("| Model | Overall | Truist-weighted | Failed requests | Median latency (s) | p95 latency (s) | "
               + " | ".join(CATEGORIES) + " |")
    out.append("|---" * (6 + len(CATEGORIES)) + "|")
    confusions = []
    for model in models:
        path = RESULTS / model / "accuracy" / "accuracy.csv"
        if not path.exists():
            continue
        rows = read_csv(path)
        n, correct = len(rows), sum(int(r["correct"]) for r in rows)
        per_cat = {}
        for c in CATEGORIES:
            in_cat = [r for r in rows if r["golden"] == c]
            if in_cat:
                per_cat[c] = (sum(int(r["correct"]) for r in in_cat), len(in_cat))
        weighted = (sum(per_cat[c][0] / per_cat[c][1] * TRUIST_MIX[c] for c in per_cat)
                    / sum(TRUIST_MIX[c] for c in per_cat))
        ok = sorted(float(r["latency_ms"]) / 1000 for r in rows if r["status"] == "201")
        failed = n - len(ok)
        cells = [f"{100 * per_cat[c][0] / per_cat[c][1]:.0f}% ({per_cat[c][0]}/{per_cat[c][1]})" if c in per_cat else "-"
                 for c in CATEGORIES]
        out.append(f"| {model} | {100 * correct / n:.1f}% ({correct}/{n}) | {100 * weighted:.1f}% | {failed} | "
                   f"{pct(ok, .5):.2f} | {pct(ok, .95):.2f} | " + " | ".join(cells) + " |")
        wrong = Counter((r["golden"], r["predicted"] or "(failed)") for r in rows if r["correct"] == "0")
        confusions.append((model, wrong.most_common(3)))
    out += ["", "Per-category cells: accuracy (correct/golden tickets). Failed requests count as incorrect.", "",
            "### Most frequent errors (golden → predicted)", ""]
    for model, top in confusions:
        out.append(f"- **{model}:** " + "; ".join(f"{g} → {p} ({k})" for (g, p), k in top))
    return out


# --- Load ---------------------------------------------------------------------

def run_stats(rows, label, duration_h):
    samples = [r for r in rows if r["label"] == label]
    if not samples:
        return None
    elapsed = sorted(int(r["elapsed"]) for r in samples)
    ok = sum(r["success"] == "true" for r in samples)
    # JMeter closes still-open connections when the schedule ends; those requests never got an answer.
    cut_off = sum(r["success"] != "true" and "Socket closed" in r["responseMessage"] for r in samples)
    errors = len(samples) - ok - cut_off
    answered = len(samples) - cut_off
    stats = {
        "n": len(samples), "ok": ok, "errors": errors, "cut_off": cut_off,
        "p50": pct(elapsed, .5) / 1000, "p95": pct(elapsed, .95) / 1000, "p99": pct(elapsed, .99) / 1000,
        "throughput": ok / duration_h, "error_rate": 100 * errors / answered if answered else 0.0,
    }
    if label == "POST /tickets":
        samples.sort(key=lambda r: int(r["timeStamp"]))
        third = max(1, len(samples) // 3)
        first = sorted(int(r["elapsed"]) for r in samples[:third])
        last = sorted(int(r["elapsed"]) for r in samples[-third:])
        stats["backlog_ok"] = pct(last, .95) <= pct(first, .95)
    return stats


def reconcile(model, rate, run, jtl_rows):
    log = RESULTS / model / "requests.log"
    if not log.exists():
        return "log not copied yet"
    run_id = f"{model}-{rate}-r{run}"
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not any("run_id" in e for e in entries):
        return "log has no run IDs (app built before run-ID logging); see session totals"
    logged = Counter(e["path"] for e in entries if e.get("run_id") == run_id)
    sent = Counter(LABEL_TO_PATH[r["label"]] for r in jtl_rows if r["label"] in LABEL_TO_PATH)
    mismatches = [f"{p}: jtl {sent[p]} vs log {logged[p]}" for p in sorted(set(sent) | set(logged)) if sent[p] != logged[p]]
    return "match" if not mismatches else "MISMATCH " + "; ".join(mismatches)


def load_section(models, duration_min):
    duration_h = duration_min / 60
    out = ["## Load tests (open-loop, mean of runs with min–max)", ""]
    verdicts, recon = [], []
    for label in ("POST /tickets", "GET /search", "GET /stats"):
        out += [f"### {label}", "",
                "| Model | Rate (/h) | Runs | Samples/run | p50 (s) | p95 (s) | p99 (s) | Achieved (/h) | "
                "Service error rate (%) | Unanswered at run end |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        for model in models:
            for rate_dir in sorted((RESULTS / model / "load").glob("*"), key=lambda p: int(p.name)):
                per_run = []
                for run_dir in sorted(rate_dir.glob("run*")):
                    jtl = run_dir / "results.jtl"
                    if not jtl.exists() or jtl.stat().st_size == 0:
                        continue
                    rows = read_csv(jtl)
                    s = run_stats(rows, label, duration_h)
                    if s:
                        per_run.append(s)
                    if label == "POST /tickets":
                        recon.append(f"| {model} | {rate_dir.name} | {run_dir.name} | "
                                     f"{reconcile(model, rate_dir.name, run_dir.name[3:], rows)} |")
                if not per_run:
                    continue
                get = lambda k: [s[k] for s in per_run]
                out.append(f"| {model} | {rate_dir.name} | {len(per_run)} | {spread(get('n'), '{:.0f}')} | "
                           f"{spread(get('p50'), '{:.2f}')} | {spread(get('p95'), '{:.2f}')} | {spread(get('p99'), '{:.2f}')} | "
                           f"{spread(get('throughput'))} | {spread(get('error_rate'))} | {spread(get('cut_off'), '{:.0f}')} |")
                if rate_dir.name == "50":
                    verdicts.append((model, label, per_run))
        out.append("")
    out += ["Service errors: the service answered with an error (e.g. 502 after a runaway answer). "
            "Unanswered at run end: still waiting when the 20-min schedule ended, so JMeter closed the connection; "
            "their recorded latency is only a lower bound, so p95/p99 are lower bounds whenever this column is non-zero. "
            "Achieved throughput counts successful tickets only.", ""]

    out += ["## Requirement verdicts at 50 tickets/h (pass only if all three runs pass)", "",
            "| Model | R1 throughput & backlog | R2 POST p95 ≤ 60 s | R3 search p95 ≤ 1 s |", "|---|---|---|---|"]
    by_model = defaultdict(dict)
    for model, label, per_run in verdicts:
        by_model[model][label] = per_run
    for model, labels in by_model.items():
        tickets, search = labels.get("POST /tickets", []), labels.get("GET /search", [])
        full = len(tickets) == 3
        r1 = full and all(s["ok"] / s["n"] >= 0.99 and s["error_rate"] < 1 and s["backlog_ok"] for s in tickets)
        r2 = full and all(s["p95"] <= 60 for s in tickets)
        r3 = len(search) == 3 and all(s["p95"] <= 1 for s in search)
        note = "" if full else " (incomplete: fewer than 3 runs)"
        out.append(f"| {model}{note} | {'PASS' if r1 else 'FAIL'} | {'PASS' if r2 else 'FAIL'} | {'PASS' if r3 else 'FAIL'} |")
    out += ["", "## Reconciliation (`.jtl` request counts vs service log, by run ID)", "",
            "| Model | Rate | Run | Result |", "|---|---|---|---|"] + recon
    out += ["", "### Session totals (all client-side records vs every service log line)", "",
            "| Model | Path | JMeter + accuracy script | Service log | Difference |", "|---|---|---|---|---|"]
    for model in models:
        log = RESULTS / model / "requests.log"
        if not log.exists():
            continue
        logged = Counter(json.loads(l)["path"] for l in log.read_text(encoding="utf-8").splitlines() if l.strip())
        sent = Counter()
        for jtl in (RESULTS / model / "load").glob("*/run*/results.jtl"):
            sent.update(LABEL_TO_PATH[r["label"]] for r in read_csv(jtl) if r["label"] in LABEL_TO_PATH)
        accuracy = RESULTS / model / "accuracy" / "accuracy.csv"
        if accuracy.exists():
            sent["/tickets"] += len(read_csv(accuracy))
        for path in ("/tickets", "/search", "/stats"):
            out.append(f"| {model} | {path} | {sent[path]} | {logged[path]} | {logged[path] - sent[path]:+d} |")
    out += ["", "A /tickets difference of +1 is the warm-up ticket, which is sent outside JMeter and the accuracy run."]
    return out


def main():
    global RESULTS
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=20, help="load run length in minutes")
    parser.add_argument("--results", type=Path, default=RESULTS)
    args = parser.parse_args()
    RESULTS = args.results
    models = sorted(p.name for p in RESULTS.iterdir() if p.is_dir() and p.name != "stress")
    lines = ["# Results summary", "", "Generated by `scripts/analyse.py` from the files in `results/`.", ""]
    lines += accuracy_section(models) + [""] + load_section(models, args.duration)
    out = RESULTS / "summary.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n".join(lines))
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
