"""Assignment 2, Step 4 set-up: measure S and fix the speed-test settings for every configuration.

  python scripts/a2_measure_s.py

Run once, on the system-under-test laptop, from the repository root, with .env set for llama3.1:8b alone
(MODEL=llama3.1:8b, no CASCADE_* values) and the stack up. Following the brief:
  1. The normal preparation (a2_prepare_run.prepare): every environment check, Ollama restart, the three
     fixed warm-up tickets (run ID s-measure-warmup), `ollama ps` showing llama3.1:8b only.
  2. Sends the three S tickets (a2/speed_extra_tickets.csv, role s_measure: three different tickets of
     typical length), one at a time, run ID s-measure. Each is sent as the speed workload sends tickets
     (whitespace collapsed to single spaces). The time is the client-side elapsed time, as JMeter measures it.
  3. S = the middle of the three times, in seconds (2 decimals). From S:
       rate      = 3600 / (2 x S) tickets per hour, rounded to a whole number
       run       = S minutes, rounded up, at least 5
       timeout   = 10 x S seconds, rounded up, at least 300
       pause     = timeout + 60 s, in whole minutes, rounded up
Writes a2/results/speed_setup.json (read by jmeter/run_a2_speed.ps1 for every run) and
a2/results/s_measure/ (prepare.json, ollama_ps.txt). Refuses to run if speed_setup.json already exists:
S is measured once and the same settings are used for every configuration.
Standard library only.
"""
import argparse
import csv
import json
import math
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from a2_prepare_run import LARGE, REPO, call, fail, ok, prepare  # noqa: E402

CLIENT_PEAK_PER_HOUR = 25  # Assignment 1 workload model (playbook.md: 25/h peak, 50/h design rate)


def settings(s):
    timeout = max(math.ceil(10 * s), 300)
    return {"rate_per_hour": int(round(3600 / (2 * s))), "duration_min": max(math.ceil(s), 5),
            "timeout_s": timeout, "pause_min": math.ceil((timeout + 60) / 60)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--service", default="http://localhost:5000")
    parser.add_argument("--ollama", default="http://localhost:11434")
    parser.add_argument("--log", type=Path, default=REPO / "logs" / "requests.log")
    args = parser.parse_args()

    setup_path = REPO / "a2" / "results" / "speed_setup.json"
    out_dir = REPO / "a2" / "results" / "s_measure"
    if setup_path.exists():
        fail(f"{setup_path.relative_to(REPO)} already exists: S has been measured")
    if (out_dir / "prepare.json").exists():
        print(f"note: replacing {out_dir.relative_to(REPO)} from an earlier attempt that did not finish")

    prepare("llama", "s-measure", out_dir, args.service, args.ollama, args.log)

    print("7. S tickets (client-side time, one at a time)")
    with open(REPO / "a2" / "speed_extra_tickets.csv", encoding="utf-8", newline="") as f:
        rows = [r["row"] for r in csv.DictReader(f) if r["role"] == "s_measure"]
    with open(REPO / "ict3113_tickets.csv", encoding="utf-8", newline="") as f:
        text = {r["row"]: re.sub(r"\s+", " ", r["narrative"]).strip() for r in csv.DictReader(f)}
    if len(rows) != 3 or len(set(rows)) != 3:
        fail(f"expected 3 different S rows in a2/speed_extra_tickets.csv, found {rows}")
    tickets = []
    for row in rows:
        status, payload, ms = call(f"{args.service}/tickets", "POST", {"narrative": text[row]}, "s-measure",
                                   timeout=600)
        if status != 201:
            fail(f"S ticket row {row}: HTTP {status} {payload}")
        tickets.append({"row": row, "chars": len(text[row]), "client_ms": ms, "ticket_id": payload.get("id"),
                        "category": payload.get("category")})
        ok(f"row {row} ({len(text[row])} chars): {ms / 1000:.2f} s -> {payload.get('category')}")

    with open(args.log, encoding="utf-8") as f:
        lines = {e.get("ticket_id"): e for e in map(json.loads, filter(str.strip, f)) if e.get("run_id") == "s-measure"}
    for t in tickets:
        e = lines.get(t["ticket_id"])
        if e is None or e.get("model") != LARGE:
            fail(f"S ticket row {t['row']}: no log line answered by {LARGE}")
        t["service_total_ms"] = e.get("total_ms")
        t["ollama_ms"] = e.get("classify_ms")

    s = round(statistics.median(t["client_ms"] for t in tickets) / 1000, 2)
    setup = {"S_s": s, **settings(s)}
    per_run = setup["rate_per_hour"] * setup["duration_min"] / 60
    setup.update({
        "tickets_per_run": round(per_run, 1),
        "client_peak_per_hour": CLIENT_PEAK_PER_HOUR,
        "test_rate_vs_client_peak": "above" if setup["rate_per_hour"] > CLIENT_PEAK_PER_HOUR else "below",
        "s_tickets": tickets,
        "method": "llama3.1:8b alone, after an Ollama restart and the three fixed warm-ups; three different "
                  "typical-length tickets sent one at a time; S = middle client-side time",
        "measured_utc": datetime.now(timezone.utc).isoformat(),
    })
    setup_path.write_text(json.dumps(setup, indent=2) + "\n", encoding="utf-8")

    times = ", ".join(f"{t['client_ms'] / 1000:.2f}" for t in tickets)
    print(f"\nS = {s} s (times {times} s; rows {', '.join(rows)})")
    print(f"  rate {setup['rate_per_hour']} tickets/hour (one every {2 * s:.1f} s on average), "
          f"run {setup['duration_min']} min (~{per_run:.0f} tickets), timeout {setup['timeout_s']} s, "
          f"pause {setup['pause_min']} min")
    print(f"  test rate is {setup['test_rate_vs_client_peak']} the client peak of {CLIENT_PEAK_PER_HOUR}/hour")
    print(f"-> {setup_path.relative_to(REPO)}, {out_dir.relative_to(REPO)}")
    print("Save the log lines:")
    print("  python scripts/a2_save_log.py --run-id s-measure-warmup --run-id s-measure "
          "--out a2/results/s_measure/requests.log")


if __name__ == "__main__":
    main()
