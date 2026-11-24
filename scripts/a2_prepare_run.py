"""Assignment 2: prepare one speed-test run on the system-under-test laptop (brief Step 4, "Each run").

  python scripts/a2_prepare_run.py --config llama --run 1
  python scripts/a2_prepare_run.py --config cascade-A --run 2

Run from the repository root on the laptop, with the stack up and .env set for the configuration. In order:
  1. Checks the service is running the configuration asked for (/health: configuration, models, escalate
     set, prompt version, seed, temperature, think), and that the environment is unchanged: Ollama version
     and all four model digests against scripts/models.lock.json, the Ollama settings in the container, the
     Docker version, no uncommitted changes to app/ or docker-compose.yaml, and the app container running
     the repository's app.py. Records the repository commit and the last commit that changed the service.
  2. Restarts the Ollama container (clears its prompt cache), waits until it answers, and checks that no
     model is loaded.
  3. Sends the three fixed warm-up tickets (a2/speed_extra_tickets.csv, original text), one at a time,
     with run ID speed-<config>-r<run>-warmup. Timings are recorded but are not results.
  4. Checks the service log for those three tickets. For a cascade, at least one must have been escalated,
     so the large model has been loaded through the cascade.
  5. Checks `ollama ps`: exactly the configuration's models are loaded (both, for a cascade), on CPU.
  6. Reads GET /stats.
Writes a2/results/speed/<config>/run<run>/prepare.json and ollama_ps.txt, then prints the command to start
the run on the load generator. Stops at the first failed check; do not start the run until this passes.
Refuses to prepare a run whose results.jtl already exists. Standard library only.
"""
import argparse
import csv
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LARGE, SMALL = "llama3.1:8b", "qwen3:4b"
EXPECTED = {
    "llama": {"configuration": "single", "model": LARGE, "cascade_small": None, "cascade_escalate": None},
    "cascade-A": {"configuration": "cascade-A", "model": LARGE, "cascade_small": SMALL,
                  "cascade_escalate": ["Credit card", "Debt collection"]},
    "cascade-B": {"configuration": "cascade-B", "model": LARGE, "cascade_small": SMALL,
                  "cascade_escalate": ["Credit reporting", "Debt collection"]},
}
INFERENCE = {"prompt_version": "v1", "seed": 89, "temperature": 0, "think": False}
OLLAMA_ENV = {"OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "2", "OLLAMA_KEEP_ALIVE": "-1"}
DOCKER_VERSION = "29.8.0"                         # a2/results/environment.txt
SERVICE_FILES = ["app", "docker-compose.yaml"]    # the code and stack definition under test


def call(url, method="GET", body=None, run_id=None, timeout=60):
    headers = {"Content-Type": "application/json"}
    if run_id:
        headers["X-Run-Id"] = run_id
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            status, payload = resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        status, payload = e.code, {"error": e.read().decode(errors="replace")[:300]}
    except (urllib.error.URLError, OSError, ValueError) as e:
        status, payload = 0, {"error": str(e)}
    return status, payload, round((time.perf_counter() - start) * 1000, 1)


def command(*args, timeout=180):
    done = subprocess.run(list(args), capture_output=True, text=True, timeout=timeout, cwd=REPO)
    if done.returncode != 0:
        fail(f"{' '.join(args)} failed: {done.stderr.strip()[:300]}")
    return done.stdout


def compose(*args, timeout=180):
    return command("docker", "compose", "-f", str(REPO / "docker-compose.yaml"), *args, timeout=timeout)


def tag(name):
    return name if ":" in name else f"{name}:latest"


def loaded(ollama):
    status, body, _ = call(f"{ollama}/api/ps", timeout=30)
    if status != 200:
        fail(f"Ollama /api/ps failed: {status} {body}")
    return body.get("models") or []


def fail(message):
    sys.exit(f"STOP: {message}\nDo not start the run.")


def ok(message):
    print(f"  ok  {message}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, choices=sorted(EXPECTED))
    parser.add_argument("--run", required=True, type=int, choices=[1, 2, 3])
    parser.add_argument("--service", default="http://localhost:5000")
    parser.add_argument("--ollama", default="http://localhost:11434")
    parser.add_argument("--log", type=Path, default=REPO / "logs" / "requests.log")
    args = parser.parse_args()

    run_id = f"speed-{args.config}-r{args.run}"
    out_dir = REPO / "a2" / "results" / "speed" / args.config / f"run{args.run}"
    if (out_dir / "results.jtl").exists():
        fail(f"{out_dir / 'results.jtl'} already exists: this run has been recorded")
    prepare(args.config, run_id, out_dir, args.service, args.ollama, args.log, run=args.run)
    print(f"\nREADY. Wrote {out_dir.relative_to(REPO)}\\prepare.json and ollama_ps.txt")
    print("Now, on the desktop (load generator):")
    print(f"  .\\jmeter\\run_a2_speed.ps1 -Config {args.config} -Run {args.run} -SutHost <laptop IP>")
    print("After the run finishes, here on the laptop:")
    print(f"  python scripts/a2_save_log.py --run-id {run_id}-warmup --run-id {run_id} "
          f"--out a2/results/speed/{args.config}/run{args.run}/requests.log")


def prepare(config, run_id, out_dir, service, ollama, log, run=None):
    """Steps 1-6 of the module docstring. Writes out_dir/prepare.json and ollama_ps.txt; returns the record.
    Warm-ups are sent with run ID <run_id>-warmup. Exits at the first failed check."""
    args = argparse.Namespace(config=config, service=service, ollama=ollama, log=log)
    record = {"run_id": run_id, "configuration": config, "run": run,
              "prepared_utc": datetime.now(timezone.utc).isoformat()}

    print(f"Preparing {run_id}")
    print("1. Configuration and environment")
    status, health, _ = call(f"{args.service}/health")
    if status != 200:
        fail(f"/health failed: {status} {health}")
    want = {**EXPECTED[args.config], **INFERENCE}
    wrong = {k: (health.get(k), v) for k, v in want.items() if health.get(k) != v}
    if wrong:
        fail("/health does not match " + args.config + ": "
             + "; ".join(f"{k} is {got!r}, expected {exp!r}" for k, (got, exp) in wrong.items())
             + "\nSet .env for this configuration and run: docker compose up -d")
    record["health"] = health
    ok(f"/health: {health['configuration']}, model {health['model']}, small {health['cascade_small']}, "
       f"escalate {health['cascade_escalate']}")

    lock = json.loads((REPO / "scripts" / "models.lock.json").read_text(encoding="utf-8"))
    status, version, _ = call(f"{args.ollama}/api/version")
    if status != 200 or version.get("version") != lock["ollama_version"]:
        fail(f"Ollama version is {version}, expected {lock['ollama_version']}")
    status, tags, _ = call(f"{args.ollama}/api/tags")
    have = {tag(m["name"]): m.get("digest") for m in tags.get("models", [])} if status == 200 else {}
    bad = [f"{m['tag']}: {have.get(m['tag'])}" for m in lock["models"] if have.get(m["tag"]) != m["digest"]]
    if bad:
        fail("model digests differ from scripts/models.lock.json: " + "; ".join(bad))
    record["ollama_version"] = version["version"]
    record["digests"] = {m["tag"]: have[m["tag"]] for m in lock["models"]}
    ok(f"Ollama {version['version']}; all {len(lock['models'])} digests match models.lock.json")

    env = dict(line.split("=", 1) for line in compose("exec", "-T", "ollama", "env").splitlines() if "=" in line)
    bad = [f"{k}={env.get(k)}" for k, v in OLLAMA_ENV.items() if env.get(k) != v]
    if bad or env.get("CUDA_VISIBLE_DEVICES", None) != "":
        fail(f"Ollama settings changed: {bad}, CUDA_VISIBLE_DEVICES={env.get('CUDA_VISIBLE_DEVICES')!r}")
    record["ollama_env"] = {k: env[k] for k in [*OLLAMA_ENV, "CUDA_VISIBLE_DEVICES"]}
    ok("Ollama settings: " + ", ".join(f"{k}={env[k]}" for k in OLLAMA_ENV) + ", no GPU")

    docker_version = command("docker", "version", "--format", "{{.Server.Version}}").strip()
    if docker_version != DOCKER_VERSION:
        fail(f"Docker is {docker_version}, expected {DOCKER_VERSION} (a2/results/environment.txt)")
    dirty = command("git", "status", "--porcelain", "--", *SERVICE_FILES).strip()
    if dirty:
        fail("uncommitted changes to the service code, so the run would not match any commit:\n" + dirty)
    running = compose("exec", "-T", "app", "cat", "/app/app.py")
    local = (REPO / "app" / "app.py").read_text(encoding="utf-8")
    if running.replace("\r\n", "\n") != local.replace("\r\n", "\n"):
        fail("the app container runs a different app.py from the repository; rebuild: docker compose up -d --build")
    record["docker_version"] = docker_version
    record["git_head"] = command("git", "rev-parse", "HEAD").strip()
    record["service_code_commit"] = command("git", "log", "-1", "--format=%H", "--", *SERVICE_FILES).strip()
    ok(f"Docker {docker_version}; repository at {record['git_head'][:7]}, service code from "
       f"{record['service_code_commit'][:7]}, no uncommitted service changes, container runs this app.py")

    print("2. Restart Ollama")
    compose("restart", "ollama")
    deadline = time.time() + 120
    while call(f"{args.ollama}/api/version", timeout=5)[0] != 200:
        if time.time() > deadline:
            fail("Ollama did not come back within 120 s of the restart")
        time.sleep(2)
    record["ollama_restarted_utc"] = datetime.now(timezone.utc).isoformat()
    if loaded(args.ollama):
        fail(f"models loaded straight after the restart: {[m.get('name') for m in loaded(args.ollama)]}")
    ok("restarted and answering; no model loaded")

    print("3. Warm-up tickets (timings discarded)")
    with open(REPO / "a2" / "speed_extra_tickets.csv", encoding="utf-8", newline="") as f:
        warmup_rows = [r["row"] for r in csv.DictReader(f) if r["role"] == "warmup"]
    with open(REPO / "ict3113_tickets.csv", encoding="utf-8", newline="") as f:
        text = {r["row"]: r["narrative"] for r in csv.DictReader(f)}
    if len(warmup_rows) != 3:
        fail(f"expected 3 warm-up rows in a2/speed_extra_tickets.csv, found {warmup_rows}")
    warm_id = f"{run_id}-warmup"
    record["warmup"] = []
    for row in warmup_rows:
        status, payload, ms = call(f"{args.service}/tickets", "POST", {"narrative": text[row]}, warm_id, timeout=600)
        record["warmup"].append({"row": row, "status": status, "ms": ms, "ticket_id": payload.get("id"),
                                 "category": payload.get("category")})
        if status != 201:
            fail(f"warm-up row {row}: HTTP {status} {payload}")
        ok(f"row {row}: HTTP 201 in {ms / 1000:.1f} s -> {payload.get('category')}")

    print("4. Service log for the warm-ups")
    ids = {w["ticket_id"] for w in record["warmup"]}
    lines = []
    for _ in range(10):
        with open(args.log, encoding="utf-8") as f:
            lines = [e for e in map(json.loads, filter(str.strip, f))
                     if e.get("run_id") == warm_id and e.get("ticket_id") in ids]
        if len(lines) == len(ids):
            break
        time.sleep(1)
    if len(lines) != len(ids):
        fail(f"found {len(lines)} of {len(ids)} warm-up lines in {args.log}")
    escalated = sum(bool(e.get("escalated")) for e in lines)
    by_id = {e["ticket_id"]: e for e in lines}
    for w in record["warmup"]:
        e = by_id[w["ticket_id"]]
        w.update(final_model=e.get("model"), small_category=e.get("small_category"), escalated=e.get("escalated"))
    if args.config == "llama":
        if any(e.get("model") != LARGE for e in lines):
            fail(f"warm-ups were not answered by {LARGE}: {[e.get('model') for e in lines]}")
        ok(f"all 3 answered by {LARGE}")
    else:
        if escalated == 0:
            fail("no warm-up ticket was escalated, so the large model was not reached through the cascade")
        ok(f"{escalated} of 3 escalated to {LARGE}" + ("" if escalated == 3 else " (expected 3; at least 1 is required)"))

    print("5. ollama ps")
    models = loaded(args.ollama)
    names = sorted(tag(m.get("model") or m.get("name", "")) for m in models)
    expected = sorted({LARGE} if args.config == "llama" else {LARGE, SMALL})
    ps_text = compose("exec", "-T", "ollama", "ollama", "ps")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "ollama_ps.txt").write_text(f"# {run_id}, after warm-up, {datetime.now(timezone.utc).isoformat()}\n"
                                           + ps_text, encoding="utf-8")
    print("    " + ps_text.strip().replace("\n", "\n    "))
    if names != expected:
        fail(f"loaded models are {names}, expected {expected}")
    on_gpu = [m.get("name") for m in models if m.get("size_vram")]
    if on_gpu:
        fail(f"models using GPU memory: {on_gpu}")
    record["ollama_ps"] = [{"name": m.get("name"), "size": m.get("size"), "size_vram": m.get("size_vram")}
                           for m in models]
    ok(f"loaded: {', '.join(names)}; all on CPU")

    print("6. /stats")
    status, stats, _ = call(f"{args.service}/stats")
    if status != 200:
        fail(f"/stats failed: {status} {stats}")
    record["stats_after_warmup"] = stats.get("total")
    ok(f"total {stats.get('total')}")

    (out_dir / "prepare.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


if __name__ == "__main__":
    main()
