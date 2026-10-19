# ReqTrace - AI-assisted requirements management and supplier response analysis

A prototype for procuring CE-marked test rigs. It combines a **structured
requirements model** with rules and an optional LLM layer to:

1. **maintain** a requirements library (duplicates, conflicts, outdated references, vague wording),
2. **select** the requirements that apply to a specific procurement case,
3. **analyse supplier responses** requirement by requirement,
4. **produce order documentation**: specification, compliance matrix and deviation list.

The AI layer supports engineers. It does not decide for them. Every output
shows where it came from: a rule or the AI, the requirement version, and the
exact supplier or case text it rests on. A reviewer can accept, reject or
override any output, and each of those decisions is written to an audit log.

## Design principles

| Principle | How it is implemented |
|---|---|
| Structured information model | [reqtrace/model.py](reqtrace/model.py): typed requirements with `applies_if`, `verification`, machine-checkable `limit`, `status` (active / under review / retired), `version`, `supersedes` |
| Nothing overwritten | [reqtrace/store.py](reqtrace/store.py): each change is a new version row; full history and audit log in SQLite |
| Rules first, AI where rules are not confident | [reqtrace/supplier.py](reqtrace/supplier.py): numeric limit check, then phrase rules; the LLM is called only below a confidence threshold |
| Traceable AI output | AI evidence must be a **verbatim quote** from the source text ([llm.py `quote_in`](reqtrace/llm.py)); otherwise it is discarded and flagged |
| Disagreement goes to a human | rule and AI disagree, low confidence, anything not compliant, or the supplier ticked "Comply" but wrote otherwise: all are flagged for review |
| Runs offline | without `GROQ_API_KEY` every AI step falls back to rules and says so |

## JD area mapping

| Thesis area | Module |
|---|---|
| Management and maintenance of requirements | [maintain.py](reqtrace/maintain.py) + Maintenance page (approve, which creates a new version, or reject with a reason) |
| Selection for a procurement case | [select.py](reqtrace/select.py): case text → features (keywords or LLM) → requirements via `applies_if` |
| Analysis of supplier responses | [supplier.py](reqtrace/supplier.py) |
| Documentation for order placement | [export.py](reqtrace/export.py): specification (.md), compliance matrix (.csv/.xlsx), deviation list |
| Transparent, reviewable, challengeable | [app.py](app.py) review UI + audit log |
| Evaluation | [eval/run_eval.py](eval/run_eval.py): rules vs AI vs hybrid ablation; see [eval/README.md](eval/README.md) |

## Quick start

```bash
pip install -r requirements.txt

# optional AI layer (Groq, OpenAI-compatible API)
#   PowerShell:  $env:GROQ_API_KEY = "..."      bash:  export GROQ_API_KEY=...
#   optional:    GROQ_MODEL (default llama-3.3-70b-versatile)

streamlit run app.py                                         # review UI

python -m reqtrace.cli maintain [--ai]                       # library findings
python -m reqtrace.cli select C1 --mode hybrid               # requirements for case C1
python -m reqtrace.cli assess C1 data/responses/C1_supplierA.csv
python -m reqtrace.cli export C1 data/responses/C1_supplierA.csv --out out/

python -m eval.run_eval                                      # writes eval/results.md
python -m unittest discover -s tests -t .                    # 14 tests
```

The database `data/reqtrace.db` is created from `data/requirements.json` on
first run. Delete it to reset.

## Results so far

Run on 2026-10-19 with `openai/gpt-oss-120b` via Groq; full table in
[eval/results.md](eval/results.md). The corpus is small, self-written and its
labels are drafts (see below), so treat these numbers as a first comparison.

| | Rules | LLM only | Hybrid |
|---|---|---|---|
| Requirement recall (8 cases) | 0.69 | 0.81 | **0.82** |
| Applicable requirements missed | 90 | 55 | **52** |
| Supplier-answer accuracy (45 answers) | 0.58 | 0.71 | **0.76** |
| Macro-F1 | 0.57 | 0.64 | **0.70** |
| Sent to human review | 32 | 26 | 32 |
| Real deviations passed without review | 1 | **0** | 1 |

What the errors show:

- **The LLM finds implied features but misses the obvious unstated ones.** It
  inferred operator access from "technicians swap the gearbox". It did not
  infer an electrical supply behind a hydraulic power unit, or noise from a
  door slammed 200,000 times. Recall is still only 0.82.
- **The LLM is cautious with supplier answers.** It tends to mark short but
  sufficient answers as partial or unclear ("E-stops at the load port and
  operator panel"). That costs review time but is the safe direction.
- **Hybrid inherits confident rule errors.** The LLM is only asked when rules
  are unsure, so a rule that is *confidently* wrong is never checked: "paper
  copy on request" read as partial, "only through the buyer's approved
  solution" read as partial, and "parts list included" read as compliant while
  the layout drawings were not editable. That last one is the one deviation
  that passed without review. The LLM-only mode caught it. An obvious next
  step is a second LLM check on every rule-accepted answer, since accepting a
  deviation costs more than reviewing a compliant answer. It has not been
  added, because tuning against this same corpus would overfit it.

## Data and its limits

- **Requirements library** (58 entries, 1 retired). Regulation entries are short
  paraphrases of Regulation (EU) 2023/1230 Annex III and related directives,
  cited by section. Standards are cited by number only; their text is
  copyrighted. Entries marked "example internal guideline" are made up to show
  what company rules could look like. Section references should be checked
  against the official texts before any real use.
- **Seeded maintenance issues.** The library deliberately contains outdated
  2006/42/EC references, near-duplicates, a conflicting noise limit, vague
  wording and missing verification methods, so the maintenance checks have
  something to find. Finding them is therefore expected, not a result.
  One finding was **not** planted: R060/R065 ("hydraulic/pneumatic systems
  shall comply with EN ISO 44xx") are flagged as duplicates because they share
  most of their wording. This is a real false positive and a case where the
  reviewer should reject the proposal.
- **Cases and supplier responses** were written for this prototype; they are
  not real procurement data. Label provenance is recorded per item. See
  [eval/README.md](eval/README.md).

## Next steps toward the thesis

Real requirement library and anonymised supplier responses · labels from
procurement specialists, with inter-annotator agreement · review-time
measurement with engineers · retrieval over long technical documents instead
of a fixed feature vocabulary · requirement-to-requirement relations
(supersedes, refines, conflicts) as an explicit graph.
