# A2 Step 2.4: smoke test of the cascade implementation

Purpose: verify that the service routes and logs correctly in every configuration before any measurement.
Not a measurement: these numbers are not used in any reported result.

- **When / where:** 2026-11-23, 14:58–15:20 UTC, on the system-under-test laptop, `scripts/a2_smoke.py`
  against `localhost:5000`, code at commit `9372324` ("Add Cascading, A and B").
- **Tickets:** the last 8 team rows outside the golden set, in this order: 3989, 3992, 3993, 3994, 3995,
  3996, 3997, 3999. The same 8 tickets were sent to each configuration.
- **Raw evidence:** `requests_smoke.log` (the 24 service-log lines with run IDs `a2-smoke-A`,
  `a2-smoke-B`, `a2-smoke-single`). The log stores narrative length only, not text.

## Results

| Ticket | qwen3:4b said | A: escalated → final | B: escalated → final | Single (llama3.1:8b) |
|---|---|---|---|---|
| 3989 | Credit reporting | no → Credit reporting | **yes** → Credit reporting | Credit reporting |
| 3992 | Debt collection | **yes** → Consumer loan | **yes** → Consumer loan | Consumer loan |
| 3993 | Debt collection | **yes** → Credit card | **yes** → Credit card | Credit card |
| 3994 | Credit card | **yes** → Credit card | no → Credit card | Credit card |
| 3995 | Money transfer or service | no → Money transfer or service | no → Money transfer or service | Money transfer or service |
| 3996 | Debt collection | **yes** → Money transfer or service | **yes** → Money transfer or service | Money transfer or service |
| 3997 | Mortgage | no → Mortgage | no → Mortgage | Mortgage |
| 3999 | Bank account or service | no → Bank account or service | no → Bank account or service | Bank account or service |

All 24 requests returned HTTP 201 with only `id`, `category` and `latency_ms` (no `model` field).

## Checks

| Check | Result |
|---|---|
| Escalation only when the small model's prediction is in the setting's list | Pass: A escalated 4/8 (Credit card, Debt collection), B 4/8 (Credit reporting, Debt collection) |
| Log records configuration, final model, small prediction, escalated, reason, per-call times | Pass |
| Single mode unchanged (llama3.1:8b only, no `small_*` fields) | Pass |
| Both models resident after escalations | Pass: `ollama ps` listed `qwen3:4b` and `llama3.1:8b` after the A run |

## Observations that shape the speed-test procedure

- **Model loading:** the first ticket of the A run took 10.1 s (qwen3:4b loading) and the first
  escalation 17.4 s (llama3.1:8b loading). Speed runs therefore warm up both models and confirm both
  with `ollama ps` before measuring.
- **Prompt cache:** repeated tickets were much faster than on first sight, e.g. ticket 3992's small
  call took 2.41 s in the A run but 0.63 s in the B run, and llama answered it in 0.60 s in the single
  run. Speed runs therefore restart the Ollama container before every run and never repeat a ticket
  within a run.
