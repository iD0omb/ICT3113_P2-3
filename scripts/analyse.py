"""Summarise every recorded run into results/summary.md (Slides 9-10) using the playbook's definitions.

  python scripts/analyse.py [--duration 20]

Reads results/<model>/accuracy/accuracy.csv, results/<model>/load/<rate>/run<n>/results.jtl and, when present,
results/<model>/requests.log (for reconciliation). Never modifies them.
"""
import argparse
import csv
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from accuracy_run import CATEGORIES, TRUIST_MIX, pct

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
LABEL_TO_PATH = {"POST /tickets": "/tickets", "GET /search": "/search", "GET /stats": "/stats"}
# Service-side durations run long versus the desktop clock: the 600 s timeout fired at 553.9 s (playbook, Deviations).
CLOCK_SCALE = 600 / 553.9
WINDOW_MS = 5 * 60_000


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


# --- Detailed reconciliation ----------------------------------------------------

def load_log(model):
    path = RESULTS / model / "requests.log"
    if not path.exists():
        return None
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def server_start_ms(entry):
    """When the app began handling the request, on the desktop's time scale (log ts is when it finished)."""
    return datetime.fromisoformat(entry["ts"]).timestamp() * 1000 - entry["total_ms"] / CLOCK_SCALE


def jtl_runs(model):
    """(run_id, rows) for every load and stress run of a model, in time order."""
    runs = []
    for jtl in (RESULTS / model / "load").glob("*/run*/results.jtl"):
        runs.append((f"{model}-{jtl.parent.parent.name}-r{jtl.parent.name[3:]}", read_csv(jtl)))
    stress = RESULTS / "stress" / model / "results.jtl"
    if stress.exists():
        runs.append((f"{model}-stress", read_csv(stress)))
    return sorted(runs, key=lambda r: min(int(x["timeStamp"]) for x in r[1]))


def log_lines_for_runs(log, runs):
    """Log lines per run: by run ID when logged, otherwise by server start time (the run's first send
    until the next run's first send). Time assignment is exact for fast requests; a ticket that waited
    across a run boundary would be attributed to the next run."""
    if any("run_id" in e for e in log):
        return {run_id: [e for e in log if e.get("run_id") == run_id] for run_id, _ in runs}
    starts = [min(int(x["timeStamp"]) for x in rows) - 5000 for _, rows in runs] + [float("inf")]
    return {run_id: [e for e in log if starts[i] <= server_start_ms(e) < starts[i + 1]]
            for i, (run_id, _) in enumerate(runs)}


def reconciliation_section(models):
    narratives = (REPO / "jmeter" / "narratives.txt").read_text(encoding="utf-8").splitlines()
    out = ["## Detailed reconciliation", "",
           "### Accuracy run: every row in `accuracy.csv` against the service log", "",
           "| Model | Stored tickets (have `ticket_id`) | Found in log | Same category in log | "
           "Failed requests (client) | Failed `/tickets` lines in log before the first load run |",
           "|---|---|---|---|---|---|"]
    for model in models:
        log, acc_path = load_log(model), RESULTS / model / "accuracy" / "accuracy.csv"
        if log is None or not acc_path.exists():
            continue
        rows = read_csv(acc_path)
        by_id = {e["ticket_id"]: e for e in log if e.get("path") == "/tickets" and e.get("ticket_id") is not None}
        stored = [r for r in rows if r["ticket_id"]]
        found = [r for r in stored if int(r["ticket_id"]) in by_id]
        same = [r for r in found if by_id[int(r["ticket_id"])].get("category") == r["predicted"]]
        runs = jtl_runs(model)
        first_load = min(int(x["timeStamp"]) for x in runs[0][1]) if runs else float("inf")
        failed_log = sum(1 for e in log if e.get("path") == "/tickets" and e["status"] != 201
                         and server_start_ms(e) < first_load)
        out.append(f"| {model} | {len(stored)} | {len(found)} | {len(same)} | "
                   f"{len(rows) - len(stored)} | {failed_log} |")

    out += ["", "### Load and stress runs: request-level matching of `POST /tickets`", "",
            "JMeter sends narratives in file order from line 1, so the k-th ticket of a run is line k of "
            "`jmeter/narratives.txt`. Log lines are ordered by server start time and compared by narrative length.", "",
            "| Model | Run | Sent (jtl) | Log lines | Same lengths (multiset) | Same length in send order | "
            "Status agrees (201↔201, error↔error) | Sent but never logged |",
            "|---|---|---|---|---|---|---|---|"]
    for model in models:
        log = load_log(model)
        if log is None:
            continue
        runs = jtl_runs(model)
        per_run = log_lines_for_runs(log, runs)
        for run_id, rows in runs:
            sent = sorted((r for r in rows if r["label"] == "POST /tickets"), key=lambda r: int(r["timeStamp"]))
            logged = sorted((e for e in per_run[run_id] if e["path"] == "/tickets"), key=server_start_ms)
            expected = [len(narratives[i % len(narratives)]) for i in range(len(sent))]
            got = [e["chars"] for e in logged]
            multiset = "yes" if Counter(got) <= Counter(expected) else "no"
            in_order = sum(a == b for a, b in zip(expected, got))
            answered = [(s, e) for s, e in zip(sent, logged) if "Socket closed" not in s["responseMessage"]]
            status_ok = sum((s["responseCode"] == "201") == (e["status"] == 201) for s, e in answered)
            out.append(f"| {model} | {run_id.removeprefix(model + '-')} | {len(sent)} | {len(logged)} | {multiset} | "
                       f"{in_order}/{min(len(sent), len(logged))} | {status_ok}/{len(answered)} | "
                       f"{max(0, len(sent) - len(logged))} |")
    out += ["", "Sent but never logged: the request was still waiting in the connection queue when JMeter closed it "
            "at the end of the run, so the app never received it.",
            "Status disagreements in the stress run: JMeter's 600 s read timeout fired first (client error) while the "
            "service still completed the ticket (logged 201), because time spent queued before the app accepted the "
            "connection counts towards JMeter's timeout but not the app's.",
            "Send-order mismatches of a few tickets: when many requests are waiting, the app's 16 threads can pick "
            "them up slightly out of arrival order; the length multiset still matches."]
    return out


# --- Stress test -----------------------------------------------------------------

def stress_section():
    out = []
    for jtl in sorted((RESULTS / "stress").glob("*/results.jtl")):
        model = jtl.parent.name
        rows = [r for r in read_csv(jtl) if r["label"] == "POST /tickets"]
        schedule = re.search(r"rate\((\d+)/hour\) random_arrivals\((\d+) min\) rate\((\d+)/hour\)",
                             (jtl.parent / "jmeter.log").read_text(encoding="utf-8", errors="replace"))
        start_rate, minutes, end_rate = (int(x) for x in schedule.groups())
        t0 = min(int(r["timeStamp"]) for r in rows)
        done = [int(r["timeStamp"]) + int(r["elapsed"]) - t0 for r in rows if r["success"] == "true"]
        out += [f"## Stress test: {model} (ramp {start_rate} → {end_rate} tickets/h over {minutes} min)", "",
                "| Window (min) | Planned arrival rate (/h) | Sent | Sent (/h) | Completed OK (% of sent) | "
                "Timeouts/errors | Unanswered at end | p50 (s) | p95 (s) | Completions finishing in window (/h) |",
                "|---|---|---|---|---|---|---|---|---|---|"]
        windows = []
        for w in range(minutes * 60_000 // WINDOW_MS):
            sent = [r for r in rows if w * WINDOW_MS <= int(r["timeStamp"]) - t0 < (w + 1) * WINDOW_MS]
            if not sent:
                continue
            ok = sum(r["success"] == "true" for r in sent)
            cut = sum(r["success"] != "true" and "Socket closed" in r["responseMessage"] for r in sent)
            elapsed = sorted(int(r["elapsed"]) for r in sent)
            finished = sum(w * WINDOW_MS <= t < (w + 1) * WINDOW_MS for t in done)
            planned = start_rate + (end_rate - start_rate) * (w + 0.5) * 5 / minutes
            windows.append(dict(w=w, sent=len(sent), ok=ok, p95=pct(elapsed, .95) / 1000,
                                planned=planned, finished=finished * 12, errors=len(sent) - ok - cut))
            out.append(f"| {w * 5}–{w * 5 + 5} | {planned:.0f} | {len(sent)} | {len(sent) * 12} | "
                       f"{ok} ({100 * ok / len(sent):.0f}%) | {len(sent) - ok - cut} | {cut} | "
                       f"{pct(elapsed, .5) / 1000:.1f} | {pct(elapsed, .95) / 1000:.1f} | {finished * 12} |")
        strict = [x for i, x in enumerate(windows)
                  if x["ok"] >= 0.9 * x["sent"] and (i == 0 or x["p95"] <= windows[i - 1]["p95"])]
        plateau = sorted(x["finished"] for x in windows)[-5:]
        r2 = next((x for x in windows if x["p95"] > 60), None)
        first_err = next((x for x in windows if x["errors"]), None)
        out += ["",
                f"- Playbook limit rule (last window with ≥ 90% completed and p95 not above the previous window): "
                f"{strict[-1]['sent'] * 12 if strict else 'none'}/h sent (window {strict[-1]['w'] * 5}–{strict[-1]['w'] * 5 + 5} min)."
                if strict else "- Playbook limit rule: no qualifying window.",
                f"- Maximum completion rate (median of the 5 busiest windows): {statistics.median(plateau):.0f} tickets/h.",
                f"- p95 first exceeds 60 s (R2) at about {r2['sent'] * 12}/h sent (window {r2['w'] * 5}–{r2['w'] * 5 + 5} min)."
                if r2 else "- p95 never exceeded 60 s.",
                f"- First timeouts/errors at about {first_err['sent'] * 12}/h sent (window {first_err['w'] * 5}–{first_err['w'] * 5 + 5} min)."
                if first_err else "- No timeouts or errors.", ""]
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
    lines += [""] + stress_section() + reconciliation_section(models)
    out = RESULTS / "summary.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n".join(lines))
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
