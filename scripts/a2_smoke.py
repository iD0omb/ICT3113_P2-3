"""Assignment 2, Step 2.4: smoke-test the running configuration with a few non-golden tickets.

  python scripts/a2_smoke.py                    # on the laptop, against localhost:5000
  python scripts/a2_smoke.py --n 10 --run-id a2-smoke-B

Sends N team-row tickets that are NOT in the golden set (taken from the end of the team's rows, so they
stay clear of the speed-test workload chosen later), one at a time. Checks each response is 201 with a
valid category and no `model` field, then prints the matching service-log lines (by run ID): which model
answered, the small model's prediction, whether it escalated and why, and the per-call times.

Standard library only. Run from the repository root on the system-under-test machine.
"""
import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from accuracy_run import CATEGORIES, REPO, read_golden  # noqa: E402


def call(url, method="GET", body=None, run_id=None, timeout=600):
    headers = {"Content-Type": "application/json"}
    if run_id:
        headers["X-Run-Id"] = run_id
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, payload = resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        status, payload = e.code, {"error": e.read().decode(errors="replace")[:200]}
    except (urllib.error.URLError, OSError) as e:
        status, payload = 0, {"error": str(e)}
    return status, payload, (time.perf_counter() - start)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--run-id", default="a2-smoke")
    parser.add_argument("--log", type=Path, default=REPO / "logs" / "requests.log")
    args = parser.parse_args()
    base = f"http://{args.host}:5000"

    status, health, _ = call(f"{base}/health")
    if status != 200:
        sys.exit(f"/health failed: {status} {health}")
    print("health:", json.dumps(health))

    golden = {g["ticket_number"] for g in read_golden(REPO / "Golden_Test_Set.csv")}
    with open(REPO / "ict3113_tickets.csv", encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["row"] not in golden and r["narrative"].strip()]
    rows.sort(key=lambda r: int(r["row"]))
    tickets = rows[-args.n:]

    problems = []
    for r in tickets:
        status, body, seconds = call(f"{base}/tickets", "POST", {"narrative": r["narrative"]}, args.run_id)
        if status != 201 or body.get("category") not in CATEGORIES:
            problems.append(f"row {r['row']}: HTTP {status} {body}")
        if "model" in body:
            problems.append(f"row {r['row']}: response reveals the model ({body['model']})")
        print(f"row {r['row']}: HTTP {status} in {seconds:5.2f} s -> {body.get('category')}  keys={sorted(body)}")

    print("\nservice log (this run ID):")
    if not args.log.exists():
        print(f"  {args.log} not found; run this on the machine running the service")
    else:
        lines = [json.loads(l) for l in args.log.read_text(encoding="utf-8").splitlines() if l.strip()]
        mine = [e for e in lines if e.get("run_id") == args.run_id and e.get("path") == "/tickets"][-len(tickets):]
        for e in mine:
            print(f"  {e.get('configuration')}: final={e.get('model')} category={e.get('category')} "
                  f"small={e.get('small_category')} escalated={e.get('escalated')} "
                  f"reason={e.get('escalation_reason') or '-'} small_ms={e.get('small_ms')} "
                  f"large_ms={e.get('large_ms')} classify_ms={e.get('classify_ms')}")
        if health.get("cascade_small"):
            esc = sum(bool(e.get("escalated")) for e in mine)
            print(f"  escalated {esc}/{len(mine)}")
            wrong = [e for e in mine if bool(e.get("escalated")) != (e.get("small_category") in
                                                                    (health.get("cascade_escalate") or []))
                     and not e.get("escalation_reason", "").startswith("small model failed")]
            if wrong:
                problems.append(f"{len(wrong)} log lines escalated against the rule")
            if any("small_category" not in e for e in mine):
                problems.append("cascade log lines missing small_category")

    print("\n" + ("PROBLEMS:\n  " + "\n  ".join(problems) if problems else "OK"))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
