# Evaluation: what it measures and what it does not

`python -m eval.run_eval` compares three modes on the same data and writes
`results.md`:

- **rules**: keywords and phrase or limit rules only
- **ai**: LLM only, with the same guardrails
- **hybrid**: rules first, LLM where rules are not confident; disagreements are flagged

## Metrics

**Requirement selection** (8 cases). Features are extracted from each case
description and compared with the labeled features. Requirements are then
selected and compared with the set the labeled features would select.
*Applicable requirements missed* is the number that matters most: a missed
requirement never reaches the supplier.

**Supplier answers** (45 answers from 2 suppliers). The metrics are accuracy,
macro-F1 over the four classes, how many answers are sent to human review,
and *deviations passed without review*. That last one counts real deviations
that the system marked compliant **and** did not flag. It is the costly error;
the others only cost reviewer time.

## Honest limits

- **Small, self-made corpus.** The cases and supplier answers were written for
  this prototype, not taken from real procurements. They were written to look
  like realistic answers, including phrasing the rules handle badly (for
  example "only through the buyer's approved solution" is compliant even though
  it contains "only"). Still, the numbers say how the approaches compare on
  this data, not how they would do in production.
- **Who labeled what.** Every label carries `labeled_by`. They are currently
  `assistant-draft`, meaning the same person or tool that wrote the data also
  labeled it. `results.md` prints this count. Do not quote the numbers as an
  evaluation until labels are verified by someone else (see below).
- **AI results depend on the model.** Results are cached in `.cache/llm/`, so
  re-runs are reproducible. A different `GROQ_MODEL` gives different numbers.
- **The feature vocabulary is fixed** (13 features). Real cases would need
  retrieval over the full library, not a fixed vocabulary.

## How to verify the labels (about 1 hour)

1. Open `eval/gold_responses.csv`. For each row, read the requirement
   (`data/requirements.json`) and the supplier answer
   (`data/responses/*.csv`). Do this **without** looking at the system's output.
2. Change `gold` if you disagree, and write your name in `verified_by`.
3. Do the same for `features` in `data/cases.json`, and set `labeled_by` to your name.
4. Re-run `python -m eval.run_eval`.

The number of disagreements with the draft labels is worth reporting by
itself: it shows how ambiguous the task is.
