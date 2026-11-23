# A2 prediction record

> **DRAFT for team review.** Not final until this notice is removed in a later commit; no Assignment 2
> benchmark runs before that commit.

Committed before the first Assignment 2 benchmark run, together with `a2/escalation_rule.md` and
`a2/speed_workload.csv`. Not revised afterwards.

## Basis

- **Already measured, not predicted** (`a2/results/limits.md`): on the 75 test tickets llama3.1:8b scores
  76.0% (57/75), so the overall limit is 66.0% and each category's limit is llama's score − 15 points.
  gemma3:4b (64.0%), qwen3:4b (62.7%) and deepseek-r1:1.5b (38.7%) fail, so the speed test covers
  llama3.1:8b alone, cascade A and cascade B.
- **Cascade evidence:** tuning-set simulation (`a2/results/simulation_tuning.md`), 75 tickets that do not
  overlap the test set.
- **Adjustment from tuning to test:** llama3.1:8b scores 84.0% on tuning but 76.0% on test, so the test
  tickets are harder. We assume each cascade keeps its tuning-set gap to llama3.1:8b (A: −2.7 points,
  B: 0), applied to llama's test score.
- **Latency assumptions:** S (llama3.1:8b on a typical ticket, after warm-up) ≈ 2.3 s, close to its A1
  median of 2.26 s, giving one ticket every ≈ 4.6 s. The speed workload is slightly longer than the golden
  set (median 922 vs 798 characters), adding about 10% to service time. Queueing at this rate adds
  waiting time that grows with how busy the model is.

## 1. Fastest configuration that satisfies both limits

**Cascade A** (qwen3:4b first, escalate on *Credit card* or *Debt collection*).

Reasons: 71% of tickets should finish after qwen3:4b alone (~1.8 s), which pulls the median below
llama3.1:8b's. On the mean, A is at break-even (29% escalated against a ~32% break-even), so the gain is
in the median, not the mean or the tail. We expect A to pass the accuracy limits on the test set, but with
a margin of only about one ticket in Consumer loan and Money transfer. B passes with more margin but is
slower than llama3.1:8b alone, because it escalates about half the tickets.

## 2. Cascade predictions, per setting

| | llama3.1:8b alone | Cascade A | Cascade B |
|---|---|---|---|
| Escalation fraction, accuracy test | — | **29%** (22/75) | **52%** (39/75) |
| Escalation fraction, speed workload (from logs) | — | **30%** | **50%** |
| Test accuracy, overall | 76.0% (57/75, measured) | **73.3% (55/75)** | **74.7% (56/75)** |
| Passes overall limit (66.0%) | yes | **yes** | **yes** |
| Passes every category limit | yes | **yes, with Consumer loan at 7/12 and Money transfer at 5/9, each one ticket above its limit** | **yes** |
| Accuracy, escalated tickets | — | **74%** | **70%** |
| Accuracy, accepted (not escalated) tickets | — | **73%** | **79%** |
| Speed test p50 (mean of 3 runs) | **2.9 s** | **2.4 s** (17% faster than llama) | **3.6 s** (24% slower than llama) |
| Speed test p95 (3 runs pooled) | **6 s** | **8 s** | **10 s** |
| Errors / unfinished requests | 0 / 0 | 0 / 0 | 0 / 0 |

Escalated/accepted accuracies are the tuning values (A 82%/81%, B 79%/89%) scaled by the same
tuning-to-test drop as the overall prediction, so they combine to the overall figure. The p50 values add
expected queueing waits to the tuning-set medians (llama 2.42 s, A 1.99 s, B 2.59 s)
scaled for the longer workload. Escalated tickets pay for both models (~4.5 s before queueing), so both
cascades have a heavier tail than llama3.1:8b alone, even where the median is lower.

## 3. Categories expected to lose the most accuracy when qwen3:4b's answer is accepted

On the tuning set, these were the wrong answers that both settings let through:

1. **Money transfer or service** (the largest loss): qwen3:4b labels transfers and scams as *Bank account
   or service*, *Credit reporting* or *Credit card*. Predictions a setting does not escalate are accepted
   (4 of A's 10 accepted errors, 3 of B's 4). On the test set (9 tickets, limit 5/9) this is the
   category most likely to fail.
2. **Credit card** (A only): qwen3:4b labels some card complaints as *Credit reporting* or *Consumer
   loan*. A accepts these, so on tuning A scored 7/10 against llama3.1:8b's 10/10. B escalates *Credit
   reporting* and loses only 1 of 10.
3. **Debt collection** (A only): qwen3:4b labels some collection complaints as *Credit reporting*, which
   A accepts (3 tickets on tuning).

**Consumer loan** should *not* lose much in either setting, although qwen3:4b alone gets only 1 of 12
on the test set: its Consumer loan errors are mostly *Debt collection* predictions, which both settings
escalate. On tuning, both A and B scored 5/7, the same as llama3.1:8b.
