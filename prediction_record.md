# Prediction Record

Committed before the first benchmark run. Not to be revised after that commit.

- Service: `docker-compose.yaml` at this commit, baseline configuration (`OLLAMA_NUM_PARALLEL=1`, `OLLAMA_MAX_LOADED_MODELS=1`, `GUNICORN_THREADS=16`)
- Models: pinned in `scripts/models.lock.json` (Ollama 0.35.1)
- Golden set: `Golden_Test_Set.csv`
- Inference hardware (system under test): laptop, AMD Ryzen AI 9 HX 370 (12 cores / 24 threads), 32 GB RAM, Windows, Docker Desktop (WSL2). CPU-only: the integrated GPU and NPU are not used.
- Load generator: separate desktop, Intel Core i5-13600KF, 32 GB RAM, Windows, connected by direct Ethernet.
- Requirements under test (from the workload model):

| ID | Requirement | Load condition |
|---|---|---|
| R1 | ≥ 50 tickets/h, < 1% errors, no growing backlog | Open-loop, 50 tickets/h, 30 min |
| R2 | `POST /tickets` p95 ≤ 60 s | 50 tickets/h |
| R3 | `GET /search` p95 ≤ 1 s | 50 tickets/h + 214 searches/h |
| R4 | ≥ 85% overall, ≥ 70% per category | 150 golden-set tickets |

---

## 1. Bottleneck

**Prediction:** Ollama's CPU inference is the bottleneck for every model. Flask and Postgres will each account for **< 1%** of `POST /tickets` latency (`db_ms` < 50 ms at every tested rate).

**Why:** `OLLAMA_NUM_PARALLEL=1` makes Ollama classify one ticket at a time. Extra requests wait in Ollama's queue while Flask threads sit idle. Prompt processing (system prompt + narrative, ~400 tokens median) dominates each request; the JSON answer is only ~10 tokens.

**Testable consequences:**
- Maximum sustainable rate ≈ 3600 / mean service time. Above it, p95 latency grows without bound within a 60-min run.
- In the logs, `classify_ms` ≈ `total_ms` for every successful request.
- During load runs, Ollama's CPU use sits near 100% while the app and Postgres containers stay under 10%.

## 2. Per-model predictions

Single-request latency = one ticket at a time, no concurrent load, model already loaded, median-length ticket.

| Model | Params | Expected accuracy (golden set) | Expected single-request latency (median) | Max sustainable rate | R1 | R2 | R4 |
|---|---|---|---|---|---|---|---|
| deepseek-r1:1.5b | 1.8B | 55% | 2 s | ~1,800/h | Pass | Pass | **Fail** |
| qwen3:4b | 4.0B | 75% | 5 s | ~700/h | Pass | Pass | **Fail** |
| gemma3:4b | 4.3B | 80% | 5 s | ~700/h | Pass | Pass | **Fail** (close) |
| llama3.1:8b | 8.0B | 83% | 10 s | ~360/h | Pass | Pass | **Fail** (close) |

**Reasoning:**
- **Latency** scales roughly with parameter count on CPU: about 2x per doubling of size. Long tickets (p95, ~450 narrative tokens) will take about 1.5x the median.
- **Accuracy:** larger models follow the category definitions more reliably. deepseek-r1:1.5b is a small model distilled for step-by-step reasoning; with thinking disabled it loses its main strength.
- **R3 (search)** passes for all models: an unindexed `ILIKE` over a few thousand rows takes milliseconds.
- **Overall:** no candidate meets every requirement. The larger models come closest on accuracy, and every model has throughput to spare at 50 tickets/h.

## 3. Hardest categories

| Rank | Category | Most likely confused with | Why |
|---|---|---|---|
| 1 | Money transfer or service | Bank account or service | Transfers usually happen from a bank account, so narratives mention both. It also had the lowest human agreement in our golden set (13 tickets agreed). |
| 2 | Bank account or service | Money transfer, Credit card | It's the broadest category: fees, deposits and account access overlap with other products. Human labellers disagreed on it most often (23 vs 29 tickets). |
| 3 | Consumer loan | Credit card, Debt collection | Loans in default get described as debt, and some personal credit lines read like card accounts. |
| 4 | Credit reporting | Debt collection | Collection accounts show up on credit reports, so many narratives dispute both at once. |

**Easiest:** Mortgage (distinctive vocabulary such as escrow, foreclosure and servicer), predicted ≥ 90% for every model.
