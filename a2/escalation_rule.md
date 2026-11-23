# A2 escalation rule (cascade specification)

Committed before the first Assignment 2 benchmark run. Held fixed for every measurement.

## 1. Models

| Role | Model | Ollama digest |
|---|---|---|
| Small (classifies every ticket first) | `qwen3:4b` | `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7` |
| Large (final answer for escalated tickets) | `llama3.1:8b` | `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e` |

Both are Assignment 1 candidates, unchanged (`scripts/models.lock.json`, Ollama 0.35.1).

## 2. Rule

For each ticket received by `POST /tickets`:

1. Send the narrative to the **small model** with the Assignment 1 classification request, unchanged:
   the same system prompt (`SYSTEM_PROMPT`, version `v1`), the same JSON schema restricting the answer
   to the seven categories, temperature 0, seed 89, `think=false`.
2. If the small model's call **fails** (HTTP error, timeout, or an answer that is not one of the seven
   categories), **escalate**.
3. Otherwise, if the small model's predicted category is in the setting's **escalate list**,
   **escalate**.
4. Otherwise, **accept** the small model's answer as final.
5. **Escalate** = send the same narrative, with the same request, to the **large model**. Its answer
   is final, with no further check.

The rule uses only the small model's predicted category: no extra model call, no confidence prompt, no
ticket length, no caching. The caller receives one category and cannot tell which model produced it.

```
small = classify(qwen3:4b, narrative)              # unchanged A1 prompt and settings
if small failed or small.category in ESCALATE:
    final = classify(llama3.1:8b, narrative)       # unchanged A1 prompt and settings
else:
    final = small
return final.category
```

## 3. The two settings

| Setting | Escalate when the small model predicts | Tuning-set result (simulated) |
|---|---|---|
| **A (speed)** | `Credit card`, `Debt collection` | 22/75 escalated (29%), 61/75 correct (81.3%), mean 2.65 s, median 1.99 s |
| **B (safety)** | `Credit reporting`, `Debt collection` | 39/75 escalated (52%), 63/75 correct (84.0%), mean 3.21 s, median 2.59 s |
| *Reference: llama3.1:8b alone* | — | 63/75 correct (84.0%), mean 2.69 s, median 2.42 s |

Both settings pass the overall and per-category limits on the tuning set (`a2/results/limits.json`).
Category names must match exactly, as listed above.

## 4. Configuration

Set in `.env`; the service refuses to start if a listed category is not one of the seven, if the list
is empty, or if the small and large models are the same.

| Configuration | `.env` |
|---|---|
| llama3.1:8b alone | `MODEL=llama3.1:8b`, `CASCADE_SMALL=` (empty) |
| Cascade A | `MODEL=llama3.1:8b`, `CASCADE_SMALL=qwen3:4b`, `CASCADE_ESCALATE=Credit card,Debt collection`, `CASCADE_SETTING=A` |
| Cascade B | `MODEL=llama3.1:8b`, `CASCADE_SMALL=qwen3:4b`, `CASCADE_ESCALATE=Credit reporting,Debt collection`, `CASCADE_SETTING=B` |

Ollama settings, identical for every configuration: `OLLAMA_NUM_PARALLEL=1`,
`OLLAMA_MAX_LOADED_MODELS=2` (Assignment 1 used 1; 2 keeps both cascade models resident),
`OLLAMA_KEEP_ALIVE=-1`, CPU only. Implementation: `app/app.py` (`triage()`).

## 5. What the service log records

Every `POST /tickets` line in `logs/requests.log` contains `configuration` (`single`, `cascade-A`,
`cascade-B`), `model` (the model that produced the final answer), `category` (the final answer) and
`classify_ms`. Cascade lines add `small_category`, `escalated` (true/false), `escalation_reason`
(`predicted <category>` or `small model failed`), `small_ms`, `large_ms` (escalated only) and each
call's Ollama timings prefixed `small_` and `large_`.

**Escalation fraction** of a run = `POST /tickets` log lines with `escalated: true` ÷ all `POST
/tickets` log lines with that run ID.

## 6. Why this rule

**Category-based, because it adds no model call.** On this machine the small models are not much
faster than llama3.1:8b (Assignment 1 medians: qwen3:4b 1.57 s, gemma3:4b 1.93 s, llama3.1:8b 2.26 s).
Rules that need a second small-model call (agreement between two models, or a separate confidence
prompt) cost about as much as calling llama3.1:8b directly, so they cannot be faster. The predicted
category is free: it comes with the small model's answer.

**It targets where the small model is wrong.** On the tuning set, qwen3:4b's answer was wrong most often
when it predicted *Debt collection* (6/15 correct; llama3.1:8b got 11/15 of the same tickets). These
are largely Consumer loan complaints that mention missed payments, the main confusion seen in
Assignment 1. Escalating on that prediction sends them to the larger model.

**Why qwen3:4b first, not gemma3:4b.** A cascade is faster on average only if the escalation fraction is
below 1 − (small latency ÷ large latency). On the tuning set that break-even is about 32% for qwen3:4b
but 21% for gemma3:4b. The fastest gemma3:4b setting that passes the limits (escalate on *Bank account
or service*, 19% escalated) was already 1% slower than llama3.1:8b on the mean.
deepseek-r1:1.5b was not considered: slower than llama3.1:8b in Assignment 1 (median 9.2 s).

**Why these two settings.** Setting A is the fastest of the 32 qwen3:4b settings that pass both limits
on the tuning set; it sits at the mean break-even (−2%) and is 18% faster on the median, with a thin
accuracy margin. Setting B keeps llama3.1:8b's tuning accuracy (84.0%) and lets only 4 wrong answers
through, at a cost in speed (+20% mean). Together they show the trade-off between speed and accuracy
margin.

## 7. Evidence

- **Design data:** tuning rows only (`a2/golden_split.csv`, 75 tickets); full tables in
  `a2/results/simulation_tuning.md`, from `scripts/a2_simulate.py`. Test rows were not used.
- **Memory:** both models stay resident together (`a2/results/memory/`): qwen3:4b 3.2 GB + llama3.1:8b
  5.6 GB of a 16.3 GB Docker VM, no reloads, `ollama ps` lists both.
- **Implementation check:** routing and logging verified for both settings and the single model
  (`a2/results/smoke/smoke_test.md`).
