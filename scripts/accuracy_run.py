"""Accuracy run (playbook 3.2-3.3): send golden-set tickets to POST /tickets one at a time and score them.

  python scripts/accuracy_run.py --check
  python scripts/accuracy_run.py --host <laptop IP> --warmup --run-id <model>-warmup
  python scripts/accuracy_run.py --host <laptop IP> --run-id <model>-acc --out results/<model>/accuracy/accuracy.csv

Standard library only. Reads the golden set without modifying it. Golden_Test_Set.csv is an Excel
workbook despite its extension; both .xlsx content and real CSV are accepted.
"""
import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CATEGORIES = [
    "Bank account or service",
    "Consumer loan",
    "Credit card",
    "Credit reporting",
    "Debt collection",
    "Money transfer or service",
    "Mortgage",
]
CANONICAL = {c.lower(): c for c in CATEGORIES}
# Truist 2024 CFPB category mix (workload model, Slide 3)
TRUIST_MIX = {
    "Bank account or service": 1529, "Credit reporting": 968, "Consumer loan": 423, "Credit card": 345,
    "Mortgage": 328, "Debt collection": 217, "Money transfer or service": 160,
}
XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def canonical(label):
    return CANONICAL.get(" ".join(label.split()).lower())


def column_index(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(path):
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{XLSX_NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{XLSX_NS}t")))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.iter(f"{XLSX_NS}row"):
        values = {}
        for c in row.iter(f"{XLSX_NS}c"):
            kind = c.get("t")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in c.iter(f"{XLSX_NS}t"))
            else:
                v = c.find(f"{XLSX_NS}v")
                if v is None:
                    continue
                value = shared[int(v.text)] if kind == "s" else v.text
            values[column_index(c.get("r"))] = value
        if values:
            rows.append([values.get(i, "") for i in range(max(values) + 1)])
    header, body = rows[0], rows[1:]
    return [dict(zip(header, r + [""] * (len(header) - len(r)))) for r in body]


def read_golden(path):
    if zipfile.is_zipfile(path):
        records = read_xlsx(path)
    else:
        with open(path, encoding="utf-8-sig", newline="") as f:
            records = list(csv.DictReader(f))
    golden = []
    for r in records:
        number = str(r["ticket_number"]).strip()
        golden.append({
            "ticket_number": number[:-2] if number.endswith(".0") else number,
            "narrative": str(r["narrative"]).strip(),
            "label": str(r["category"]).strip(),
        })
    return golden


def check(golden):
    """Validate the golden set before any request is sent. Returns the list of problems."""
    problems = []
    unknown = sorted({g["label"] for g in golden if canonical(g["label"]) is None})
    if unknown:
        problems.append(f"labels outside the 7 categories: {unknown}")
    empty = [g["ticket_number"] for g in golden if not g["narrative"]]
    if empty:
        problems.append(f"empty narratives: {empty}")
    dupes = [n for n, k in Counter(g["ticket_number"] for g in golden).items() if k > 1]
    if dupes:
        problems.append(f"duplicate ticket numbers: {dupes}")
    print(f"golden set: {len(golden)} tickets")
    for label, n in Counter(canonical(g["label"]) or g["label"] for g in golden).most_common():
        print(f"  {label:28s} {n}")
    for p in problems:
        print("PROBLEM:", p)
    return problems


def post_ticket(base, narrative, run_id, timeout):
    request = urllib.request.Request(
        f"{base}/tickets", data=json.dumps({"narrative": narrative}).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Run-Id": run_id},
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            status, payload = resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        status, payload = e.code, {"error": e.read().decode(errors="replace")[:300]}
    except (urllib.error.URLError, OSError) as e:
        status, payload = 0, {"error": str(e)}
    return status, payload, round((time.perf_counter() - start) * 1000, 1)


def pct(sorted_values, p):
    """Nearest-rank percentile, as in the playbook."""
    return sorted_values[min(len(sorted_values) - 1, int(p * len(sorted_values)))]


def summarise(rows, out):
    n = len(rows)
    correct = sum(r["correct"] for r in rows)
    print(f"\noverall accuracy: {correct}/{n} = {100 * correct / n:.1f}%")

    per_category = {}
    print(f"{'category':28s} {'correct':>7s} {'n':>4s} {'acc':>6s}")
    for c in CATEGORIES:
        in_cat = [r for r in rows if r["golden"] == c]
        if in_cat:
            acc = sum(r["correct"] for r in in_cat) / len(in_cat)
            per_category[c] = acc
            print(f"{c:28s} {sum(r['correct'] for r in in_cat):7d} {len(in_cat):4d} {100 * acc:5.1f}%")
    weight = sum(TRUIST_MIX[c] for c in per_category)
    weighted = sum(per_category[c] * TRUIST_MIX[c] for c in per_category) / weight
    print(f"accuracy weighted by Truist category mix: {100 * weighted:.1f}%")

    ok = sorted(r["latency_ms"] for r in rows if r["status"] == 201)
    errors = n - len(ok)
    if ok:
        print(f"single-request latency (successful, n={len(ok)}): median {pct(ok, .5) / 1000:.2f} s, "
              f"p95 {pct(ok, .95) / 1000:.2f} s, max {ok[-1] / 1000:.2f} s; failed requests: {errors}")

    columns = CATEGORIES + ["(failed)"]
    matrix = Counter((r["golden"], r["predicted"] or "(failed)") for r in rows)
    confusion = out.with_name("confusion.csv")
    with open(confusion, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["golden \\ predicted"] + columns)
        for g in sorted({r["golden"] for r in rows}, key=lambda c: (c not in CATEGORIES, c)):
            w.writerow([g] + [matrix[(g, p)] for p in columns])
    print(f"confusion matrix -> {confusion}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--golden", type=Path, default=REPO / "Golden_Test_Set.csv")
    parser.add_argument("--narratives", type=Path, default=REPO / "jmeter" / "narratives.txt")
    parser.add_argument("--run-id")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--limit", type=int, help="send only the first N golden tickets (testing)")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--check", action="store_true", help="validate the golden set only; send nothing")
    parser.add_argument("--warmup", action="store_true", help="send one non-golden ticket to load the model")
    parser.add_argument("--allow-unknown", action="store_true",
                        help="score labels outside the 7 categories as incorrect instead of aborting")
    args = parser.parse_args()

    golden = read_golden(args.golden)
    problems = check(golden)
    if args.check:
        sys.exit(1 if problems else 0)
    if problems and not (args.allow_unknown and all(p.startswith("labels outside") for p in problems)):
        sys.exit("aborting: fix the golden set problems above first")
    if not args.host or not args.run_id:
        sys.exit("--host and --run-id are required")
    base = f"http://{args.host}:{args.port}"

    if args.warmup:
        golden_text = {" ".join(g["narrative"].split()) for g in golden}
        lines = args.narratives.read_text(encoding="utf-8").splitlines()
        narrative = next(line for line in lines if line.strip() and line.strip() not in golden_text)
        status, payload, ms = post_ticket(base, narrative, args.run_id, args.timeout)
        print(f"warm-up: HTTP {status} in {ms / 1000:.2f} s -> {payload}")
        sys.exit(0 if status == 201 else 1)

    if not args.out:
        sys.exit("--out is required for an accuracy run")
    if args.out.exists():
        sys.exit(f"{args.out} already exists; refusing to overwrite a recorded run")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tickets = golden[: args.limit] if args.limit else golden

    rows = []
    fields = ["ticket_number", "golden", "predicted", "correct", "status", "latency_ms", "ticket_id", "error"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        for i, g in enumerate(tickets, 1):
            status, payload, ms = post_ticket(base, g["narrative"], args.run_id, args.timeout)
            label = canonical(g["label"]) or g["label"]
            predicted = payload.get("category", "") if status == 201 else ""
            row = {
                "ticket_number": g["ticket_number"], "golden": label, "predicted": predicted,
                "correct": int(predicted == label), "status": status, "latency_ms": ms,
                "ticket_id": payload.get("id", ""), "error": "" if status == 201 else payload.get("error", ""),
            }
            writer.writerow(row)
            f.flush()
            rows.append(row)
            print(f"[{i}/{len(tickets)}] #{g['ticket_number']} HTTP {status} {ms / 1000:6.2f} s  "
                  f"{label} -> {predicted or row['error'][:60]}")
    summarise(rows, args.out)


if __name__ == "__main__":
    main()
