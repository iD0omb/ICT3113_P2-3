# Why the cascade was not faster than llama3.1:8b alone

Written after the speed tests, for Slides 6–8. Every figure comes from a generated report:

- `a2/results/cascade_analysis.md` (`scripts/a2_cascade_analysis.py`): from the speed-test service logs and `.jtl` files
- `a2/results/accuracy_report.md`: accuracy-test results
- `a2/results/simulation_tuning.md`: the tuning-set evidence the rule was designed on

The prediction record (`a2/prediction_record.md`) is unchanged. This document explains where it was wrong.

## Verdict

Both cascade settings pass the accuracy limits. Neither is faster than llama3.1:8b alone.

| | Mean of p50 (SD) | Mean latency | Pooled p95 (n = 180) | Test accuracy |
|---|---|---|---|---|
| llama3.1:8b alone | 4.90 s (0.53) | 5.27 s | 10.72 s | 76.0% |
| Cascade A | 5.02 s (0.28) | 5.72 s | 11.77 s | 74.7% |
| Cascade B | 4.90 s (0.27) | 5.60 s | 11.52 s | 78.7% |

- **Median:** llama and B are level, and A is 0.12 s slower. All three differences are well inside the run-to-run spread.
- **Mean and p95:** llama is clearly lower than either cascade.

So the fastest qualifying configuration is llama3.1:8b alone.

## The three causes

1. **Both cascades escalated well above break-even.** That alone makes them slower on average, even with no other effect.
2. **The two models shared the CPU.** Under load, qwen3:4b and llama3.1:8b often computed at the same time on different tickets, and each slowed the other. This cost the cascade its median advantage.
3. **The machine was busier with the cascades** (about 80–90% against about 60% for llama alone), so tickets waited longer.

Cause 1 is about the small model's answers, cause 2 about the cascade sharing one machine, and cause 3 follows from the first two.

## 1. Escalation above break-even

The brief's approximation: a cascade's mean latency is about small + escalation × large. The cascade is faster on average only if escalation stays below break-even = 1 − small / large.

| | Escalated under load | Break-even | Tuning-set escalation (design) |
|---|---|---|---|
| Cascade A (escalate Card, Debt) | 84/180 (47%) | 30% | 22/75 (29%) |
| Cascade B (escalate Credit reporting, Debt) | 99/180 (55%) | 36% | 39/75 (52%) |

Break-even is computed from the speed-test logs: qwen3:4b's compute when it wasn't sharing the CPU (2.05 s in A, 1.87 s in B), against llama3.1:8b's compute alone (2.93 s).

**Why A escalated far more than on tuning.** qwen3:4b's mix of predicted categories differs between the tuning tickets and every other set:

| qwen3:4b predicted… | Tuning set (75) | Test set (75) | Speed workload (180) |
|---|---|---|---|
| Debt collection | 15 (20%) | 27 (36%) | 63 (35%) |
| Credit card | 7 (9%) | 5 (7%) | 21 (12%) |
| Credit reporting | 24 (32%) | 16 (21%) | 36 (20%) |

- **The tuning set was the outlier.** On the test and workload tickets, qwen3:4b predicts Debt collection almost twice as often. On the test set, 11 of the 12 Consumer loan tickets were labelled Debt collection (`accuracy_report.md`, qwen3:4b confusion matrix).
- **The escalate lists had to include Debt collection.** It was qwen3:4b's least reliable predicted category on tuning (6/15 correct, `simulation_tuning.md`), and escalating it protects the Consumer loan limit.
- **So the extra Debt collection predictions became escalations.** Under A that took escalation from 29% to 47%. Under B the rise in Debt collection was partly offset by fewer Credit reporting predictions, so escalation moved only from 52% to 55%.

This is a property of the small model, not of the machine. The tuning set gave a misleadingly low escalation estimate for A.

## 2. The two models shared the CPU

`OLLAMA_NUM_PARALLEL=1` held throughout: each model worked on one ticket at a time. The count of same-model overlaps is 0 for llama alone, and 2 (A) and 9 (B) in the cascades, out of hundreds of calls, which is within the log-timestamp error. But with both models loaded, each has its own Ollama runner. While llama3.1:8b was finishing an escalated ticket, qwen3:4b could start on the next one, on the same CPU cores.

| | qwen3:4b calls computing while llama3.1:8b computed | llama3.1:8b calls computing while qwen3:4b computed |
|---|---|---|
| Cascade A | 92/180 (51%) | 64/84 (76%) |
| Cascade B | 93/180 (52%) | 71/99 (72%) |

Sharing slowed both models (median tokens per second; the like-for-like comparison):

| | Prompt processing, alone → sharing | Generation, alone → sharing |
|---|---|---|
| qwen3:4b (A) | 282 → 200 tok/s | 25.1 → 15.7 tok/s |
| qwen3:4b (B) | 296 → 212 tok/s | 25.1 → 18.6 tok/s |
| llama3.1:8b (A) | 176 → 121 tok/s | 16.8 → 11.2 tok/s |
| llama3.1:8b (B) | 173 → 131 tok/s | 16.7 → 12.1 tok/s |

That is roughly 25–40% slower whenever both were computing. Shared qwen3:4b calls averaged 2.73 s (A) and 2.65 s (B) of compute, against 2.05 s and 1.87 s when not sharing, and the ~1.8 s measured one ticket at a time in Assignment 1. The prediction assumed the two calls would take turns.

None of this breaks the brief's rules:
- **Ollama settings** (NUM_PARALLEL 1, MAX_LOADED_MODELS 2, KEEP_ALIVE −1) were the same for every configuration, and checked before every run.
- **Both models loaded** is a hard requirement for the cascade.
- **Within one ticket, the cascade is still sequential:** small model, then large model if escalated.

The overlap happens between different tickets, because the speed test is open loop. It is disclosed on Slide 3.

## 3. Load and queueing

Model compute as a share of run time: llama 58–61%, A 83–86%, B 81–89%.

- **llama alone was already above the brief's intended ~50%.** The workload tickets take longer than the S tickets: 2.93 s compute against S = 2.47 s, because the workload's median is 922 characters against the team set's 798.
- **The cascades did more total compute,** because of causes 1 and 2, so arrivals more often found a model busy.
- **In every configuration, the median latency by third of run moves up and down with no steady rise,** so no queue built up over a run. The comparison is at a rate each configuration could keep up with, as the brief requires.

## How much each cause matters

This estimate removes only the CPU sharing:
- qwen3:4b is given its average compute time from calls where it wasn't sharing the CPU.
- llama3.1:8b is given its compute time on the same ticket from the llama-alone run with the same run number (same tickets, same order).
- Queueing isn't modelled.

| Compute per ticket | Measured mean / median | No-sharing estimate mean / median | llama3.1:8b alone mean / median |
|---|---|---|---|
| Cascade A | 4.21 s / 3.46 s | 3.28 s / 2.05 s | 2.93 s / 2.85 s |
| Cascade B | 4.22 s / 3.70 s | 3.35 s / 3.53 s | 2.93 s / 2.85 s |

- **Sharing cost about 0.9 s of compute per ticket** in both settings.
- **Without sharing, A's median would have been below llama's** (2.05 s against 2.85 s): just over half of A's tickets finish after qwen3:4b alone. This is the effect the prediction expected ("the gain is in the median, not the mean").
- **Even without sharing, both cascades' means stay above llama's** (3.28 s and 3.35 s against 2.93 s), because escalation is above break-even. B's median also stays above llama's, because most of its tickets are escalated.

**Conclusion:** with this small model and these escalate lists, the cascade could not beat llama3.1:8b on average on this machine, even if the two models had never slowed each other down. The sharing then removed the one advantage it would have had, the median.

## What this does and does not show

- **More compute is out of scope.** The brief says "the client is not adding compute or memory". On this machine, CPU sharing between the two models is part of what the cascade really costs. More cores would also speed up llama3.1:8b alone, so the comparison would not simply reverse.
- **Running one model at a time was not tested.** The service could hold a lock so only one model call runs at a time: no sharing, but longer queues. Whether that would be faster overall is not known, and it is a different design from the one tested.
- **The no-sharing figures are estimates**, with the assumptions above. They explain the result; they are not a measured configuration and must not be presented as one.
- **One machine, one arrival rate, 180 requests per configuration.** The median differences between configurations are inside the run-to-run spread.

## Prediction vs outcome (Slide 7)

| Predicted | Measured | Main reason for the gap |
|---|---|---|
| Cascade A fastest | llama3.1:8b alone fastest; A slowest | Causes 1 and 2 |
| Escalation under load A 30%, B 50% | A 47%, B 55% | The tuning set's category mix (cause 1) |
| p50 llama 2.9 s, A 2.4 s, B 3.6 s | 4.90 s, 5.02 s, 4.90 s | Queueing at ~60% load and longer workload tickets; for the cascades, CPU sharing too |
| p95 6 / 8 / 10 s | 10.72 / 11.77 / 11.52 s | Same as above; llama lowest, as predicted |
| Accuracy A 73.3%, B 74.7%, both pass | A 74.7%, B 78.7%, both pass | Close. B accepted correct qwen3:4b answers llama gets wrong |
| Money transfer loses the most | No loss; only Credit card in A (3/7 vs 4/7) | Too few tickets for category-level predictions to hold |
