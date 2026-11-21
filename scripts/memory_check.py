"""Assignment 2, Step 2 (Memory): check that the cascade's models stay loaded in Ollama together.

Run on the system-under-test laptop (the Assignment 1 machine) from the repository root, with the
stack up (docker compose up -d), no load test running, and OLLAMA_MAX_LOADED_MODELS in .env at least
the number of models checked:

  python scripts/memory_check.py qwen3:4b llama3.1:8b
  python scripts/memory_check.py gemma3:4b llama3.1:8b --tickets 5

The brief's check is "load both models, run a few tickets through each, and confirm that ollama ps
lists both". This script:
  1. records the Ollama version, the Ollama container's OLLAMA_* settings, CPU count and memory
     (through docker compose exec), and compares each model's digest with scripts/models.lock.json;
  2. unloads every loaded model, so the check starts with nothing in memory;
  3. sends the POST /tickets classification request (prompt and categories read from app/app.py)
     to the models in turn, one ticket at a time, so every call switches model as an escalation
     does. Tickets are team rows outside the golden set, a new one each round, so Ollama's prompt
     cache is never hit;
  4. reads /api/ps after every call. PASS needs every call answered, every model still listed
     after every call, and no reload (load_duration above 0.5 s) after a model's first call;
  5. saves the evidence, including `ollama ps` output, to a2/results/memory/<UTC time>_<models>/.

If a pair fails, try a smaller pair. If none passes, the cascade is not feasible on this machine
and the evidence folders are what the report shows.

Standard library only. Never overwrites evidence: each run gets a new folder.
"""
import argparse
import ast
import csv
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from accuracy_run import REPO, read_golden  # noqa: E402

RELOAD_S = 0.5  # load_duration above this, after a model's first call, means the model was reloaded
TEMPERATURE, SEED, THINK = 0, 89, False  # fixed inference settings (playbook, section 1)
FIELDS = ["call", "round", "model", "ticket_row", "http_status", "category", "wall_ms", "load_ms",
          "total_ms", "prompt_eval_count", "eval_count", "first_call_to_model", "loaded_after", "error"]
_no_think = set()


def app_constants():
    """SYSTEM_PROMPT and CATEGORIES from app/app.py, parsed rather than imported (importing opens the database)."""
    found = {}
    for node in ast.parse((REPO / "app" / "app.py").read_text(encoding="utf-8")).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ("SYSTEM_PROMPT", "CATEGORIES")):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    return found["SYSTEM_PROMPT"], found["CATEGORIES"]


def tag(name):
    return name if ":" in name else f"{name}:latest"


def api(base, method, path, body=None, timeout=600):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"{base}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")[:300]}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, {"error": str(e)}


def loaded(base):
    status, body = api(base, "GET", "/api/ps", timeout=30)
    return (body.get("models") or []) if status == 200 else []


def names(ps):
    return sorted(tag(m.get("model") or m.get("name", "")) for m in ps)


def docker(*command):
    """Run a command in the Ollama container. None when Docker or the container is not reachable."""
    try:
        done = subprocess.run(
            ["docker", "compose", "-f", str(REPO / "docker-compose.yaml"), "exec", "-T", "ollama", *command],
            capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def container_facts():
    facts = {}
    env = docker("env")
    if env is not None:
        facts["ollama_env"] = dict(line.split("=", 1) for line in env.splitlines()
                                   if line.startswith(("OLLAMA_", "CUDA_VISIBLE_DEVICES=")))
    meminfo = docker("cat", "/proc/meminfo")
    for line in (meminfo or "").splitlines():
        key, _, value = line.partition(":")
        if key in ("MemTotal", "MemAvailable") and value.split():
            facts[f"{key}_bytes"] = int(value.split()[0]) * 1024
    cpus = (docker("nproc") or "").strip()
    if cpus.isdigit():
        facts["cpus"] = int(cpus)
    return facts


def gb(n):
    return f"{n / 1e9:.1f} GB" if n else "?"


def pick_tickets(n):
    golden = {g["ticket_number"] for g in read_golden(REPO / "Golden_Test_Set.csv")}
    with open(REPO / "ict3113_tickets.csv", encoding="utf-8", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["row"] not in golden and r["narrative"].strip()]
    rows.sort(key=lambda r: int(r["row"]))
    return [(r["row"], " ".join(r["narrative"].split())) for r in rows[:n]]


def classify(base, model, narrative, prompt, schema):
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": narrative}],
        "format": schema,
        "stream": False,
        "options": {"temperature": TEMPERATURE, "seed": SEED},
    }
    if model not in _no_think:
        payload["think"] = THINK
    start = time.perf_counter()
    status, body = api(base, "POST", "/api/chat", payload)
    if status == 400 and "think" in payload and "think" in body.get("error", "").lower():
        _no_think.add(model)  # no thinking mode: retry without the flag, as app.py does
        payload.pop("think")
        start = time.perf_counter()
        status, body = api(base, "POST", "/api/chat", payload)
    return status, body, round((time.perf_counter() - start) * 1000, 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("models", nargs="+", help="models in calling order, smallest first: qwen3:4b llama3.1:8b")
    parser.add_argument("--tickets", type=int, default=3, help="rounds; every model gets one new ticket per round")
    parser.add_argument("--ollama", default="http://localhost:11434", help="Ollama URL (default: %(default)s)")
    parser.add_argument("--out", type=Path, default=REPO / "a2" / "results" / "memory",
                        help="parent folder for the evidence (default: a2/results/memory)")
    args = parser.parse_args()
    models = list(dict.fromkeys(tag(m) for m in args.models))
    if len(models) < 2:
        sys.exit("give at least two different models")
    if args.tickets < 1:
        sys.exit("--tickets must be at least 1")
    base = args.ollama.rstrip("/")
    prompt, categories = app_constants()
    schema = {"type": "object", "properties": {"category": {"type": "string", "enum": categories}},
              "required": ["category"]}  # RESPONSE_SCHEMA in app/app.py
    started = datetime.now(timezone.utc)

    status, version = api(base, "GET", "/api/version", timeout=30)
    if status != 200:
        sys.exit(f"Ollama not reachable at {base}: {version.get('error') or status}")
    print(f"Ollama {version.get('version')} at {base}")

    lock = {m["tag"]: m["digest"] for m in
            json.loads((REPO / "scripts" / "models.lock.json").read_text(encoding="utf-8"))["models"]}
    _, tags = api(base, "GET", "/api/tags", timeout=30)
    installed = {tag(m.get("model") or m.get("name", "")): m.get("digest") for m in tags.get("models") or []}
    missing = [m for m in models if m not in installed]
    if missing:
        sys.exit(f"not installed in Ollama: {', '.join(missing)}")
    digests = {m: {"installed": installed[m], "lock": lock.get(m), "match": installed[m] == lock.get(m)}
               for m in models}
    for m, d in digests.items():
        print(f"  {m:20s} digest {'matches' if d['match'] else 'DIFFERS FROM'} scripts/models.lock.json")

    ps_initial = loaded(base)
    for m in ps_initial:
        api(base, "POST", "/api/generate", {"model": m.get("model") or m.get("name"), "keep_alive": 0,
                                            "stream": False}, timeout=120)
    deadline = time.monotonic() + 60
    while loaded(base):
        if time.monotonic() > deadline:
            sys.exit("models still loaded after unloading; restart Ollama (docker compose restart ollama) and rerun")
        time.sleep(1)
    if ps_initial:
        print(f"  unloaded {', '.join(names(ps_initial))}")

    facts = container_facts()
    env = facts.get("ollama_env")
    if env is None:
        print("WARNING: could not reach the Ollama container through docker compose, so its settings and memory "
              f"are not recorded. Make sure OLLAMA_MAX_LOADED_MODELS >= {len(models)}.")
    else:
        print("  container: " + ", ".join(f"{k}={v}" for k, v in sorted(env.items())))
        limit = env.get("OLLAMA_MAX_LOADED_MODELS", "")
        if limit.isdigit() and 0 < int(limit) < len(models):
            sys.exit(f"OLLAMA_MAX_LOADED_MODELS={limit} in the running container, fewer than the {len(models)} "
                     "models checked. Set it in .env, run docker compose up -d, then rerun.")
        print(f"  nothing loaded: {gb(facts.get('MemTotal_bytes'))} total, "
              f"{gb(facts.get('MemAvailable_bytes'))} available, {facts.get('cpus', '?')} CPUs")

    tickets = pick_tickets(args.tickets)
    label = "+".join(m.replace(":", "_").replace(".", "_") for m in models)
    out = args.out / f"{started:%Y%m%dT%H%M%SZ}_{label}"
    out.mkdir(parents=True)
    print(f"\n{len(tickets)} rounds x {len(models)} models; tickets (team rows outside the golden set): "
          + ", ".join(row for row, _ in tickets))

    problems, seen = [], []
    with open(out / "calls.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, FIELDS)
        writer.writeheader()
        for rnd, (row, narrative) in enumerate(tickets, start=1):
            for model in models:
                status, body, wall_ms = classify(base, model, narrative, prompt, schema)
                try:
                    category = json.loads(body.get("message", {}).get("content", ""))["category"]
                except (ValueError, KeyError, TypeError, AttributeError):
                    category = ""
                first = model not in seen
                if first:
                    seen.append(model)
                after = names(loaded(base))
                load_s = (body.get("load_duration") or 0) / 1e9
                n = (rnd - 1) * len(models) + models.index(model) + 1
                error = "" if status == 200 and category in categories else (
                    body.get("error") or f"answer not a category: {category!r}")
                writer.writerow({
                    "call": n, "round": rnd, "model": model, "ticket_row": row, "http_status": status,
                    "category": category, "wall_ms": wall_ms, "load_ms": round(load_s * 1000, 1),
                    "total_ms": round((body.get("total_duration") or 0) / 1e6, 1),
                    "prompt_eval_count": body.get("prompt_eval_count"), "eval_count": body.get("eval_count"),
                    "first_call_to_model": int(first), "loaded_after": " ".join(after), "error": error,
                })
                f.flush()
                gone = [m for m in seen if m not in after]
                if error:
                    problems.append(f"call {n} ({model}): {error[:150]}")
                if gone:
                    problems.append(f"call {n} ({model}): ollama ps no longer lists {', '.join(gone)}")
                if not first and load_s > RELOAD_S:
                    problems.append(f"call {n} ({model}): reloaded (load_duration {load_s:.1f} s)")
                print(f"[{n}] row {row}  {model:20s} HTTP {status}  {wall_ms / 1000:6.2f} s  "
                      f"load {load_s:5.2f} s  loaded after: {', '.join(after) or '(none)'}")

    ps_final = loaded(base)
    after_facts = container_facts()
    cli = docker("ollama", "ps")
    verdict = "FAIL" if problems else "PASS"
    record = {
        "verdict": verdict, "problems": problems, "models": models,
        "started_utc": started.isoformat(), "finished_utc": datetime.now(timezone.utc).isoformat(),
        "ollama_url": base, "ollama_version": version.get("version"),
        "settings": {"temperature": TEMPERATURE, "seed": SEED, "think": THINK, "reload_threshold_s": RELOAD_S},
        "digests": digests, "ticket_rows": [row for row, _ in tickets],
        "container_nothing_loaded": facts, "container_after": after_facts,
        "ps_before_unload": ps_initial, "ps_after": ps_final,
    }
    (out / "check.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    (out / "ollama_ps.txt").write_text(
        cli if cli is not None else "docker compose exec was not available; see ps_after in check.json\n",
        encoding="utf-8")

    sizes = {tag(m.get("model") or m.get("name", "")): m.get("size") for m in ps_final}
    print(f"\n{verdict}: {' + '.join(models)}")
    for m in models:
        print(f"  {m:20s} in memory: {gb(sizes.get(m))}")
    total = sum(s for s in sizes.values() if s)
    print(f"  together: {gb(total)}" + (f" of {gb(facts['MemTotal_bytes'])} in the Docker VM"
                                         if facts.get("MemTotal_bytes") else ""))
    if after_facts.get("MemAvailable_bytes"):
        print(f"  still available: {gb(after_facts['MemAvailable_bytes'])}")
    for p in problems:
        print("  PROBLEM:", p)
    if not all(d["match"] for d in digests.values()):
        print("  WARNING: a digest differs from scripts/models.lock.json; A2 must use the A1 digests")
    print(f"evidence -> {out}")
    sys.exit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
