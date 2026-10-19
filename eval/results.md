# Evaluation results (2026-10-19)

AI model: `openai/gpt-oss-120b` via Groq

See eval/README.md for what these numbers do and do not show.

## 1. Requirement selection (8 cases)

| Mode | Feature P / R | Requirement P / R | Applicable requirements missed |
|---|---|---|---|
| rules | 0.96 / 0.56 | 0.99 / 0.69 | 90 |
| ai | 0.94 / 0.74 | 0.96 / 0.81 | 55 |
| hybrid | 0.92 / 0.77 | 0.95 / 0.82 | 52 |

Case labels by: {'assistant-draft': 8}

## 2. Supplier answer classification (45 answers, 2 suppliers)

| Mode | Accuracy | Macro-F1 | Sent to human review | Deviations passed without review |
|---|---|---|---|---|
| rules | 0.58 | 0.57 | 32/45 | 1 (C1_supplierA:R004) |
| ai | 0.71 | 0.64 | 26/45 | 0  |
| hybrid | 0.76 | 0.70 | 32/45 | 1 (C1_supplierA:R004) |

Answer labels by: {'assistant-draft': 45}; human-verified: 0/45

