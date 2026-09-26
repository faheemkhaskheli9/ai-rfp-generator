# RFP Generation using LLM

> LLM, RAG & Agentic AI portfolio project — independent open-source implementation.
> This is an original, from-scratch build. It is not affiliated with, and does not
> contain any code, prompts, data, or business logic from, any employer or client.

![status](https://img.shields.io/badge/status-in%20progress-yellow)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![license](https://img.shields.io/badge/license-MIT-green)

## 1. Problem

Writing RFP responses is repetitive and time-consuming. A multi-stage pipeline can turn requirements into a structured, fact-checked draft ready for review.

## 2. Architecture

```text
Requirement -> Document Structure -> Fact Extraction -> Section Generation -> Validation -> Final RFP Export
```

## 3. Technology Stack

- Python
- OpenAI API
- python-docx
- FastAPI
- PostgreSQL

## 4. Feature List

- Requirement intake
- Document structure planning
- Fact extraction from source materials
- Section-by-section generation
- Validation pass
- Multiple generation-strategy support
- Prompt configuration and experimentation
- Automatic evaluation
- Export to DOCX/PDF

## 5. Implementation Plan

1. Phase 1: Requirement intake and outline generation
2. Phase 2: Fact extraction and section drafting pipeline
3. Phase 3: Validation and prompt-experimentation framework
4. Phase 4: DOCX/PDF export

## Task Tracking

Work is broken into phase-tagged user stories tracked as GitHub Issues, not in this file. To see what's open:

    gh issue list --repo faheemkhaskheli9/ai-rfp-generator --state open --label type:user-story

Implement Phase 1 issues first (later phases depend on it). When you start one, add label `status:in-progress`. When you finish, close it referencing the commit (e.g. `git commit -m "... Closes #4"`) and push.

## 6. Repository Structure

```text
ai-rfp-generator/
├── README.md
├── LICENSE
├── .gitignore
├── pyproject.toml
├── .env.example
├── docker/
├── docs/
│   ├── architecture.md
│   └── evaluation.md
├── src/
├── tests/
├── configs/
├── scripts/
├── notebooks/
├── examples/
├── assets/
└── .github/
    └── workflows/
```

## 7. Setup

```bash
git clone <this-repo-url>
cd ai-rfp-generator
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # or: pip install -e .
cp .env.example .env              # fill in API keys / config
```

## 8. Dataset

Document which public dataset(s) or synthetic data generators are used here.
No proprietary, employer-owned, or client-identifiable data is used in this project.

## 9. Training / Execution

Phase 1 ships the requirement-intake API: `POST /requirements` accepts pasted
text or an uploaded `.txt`/`.docx`/`.pdf` file (multipart form), extracts
plain text, and persists it. Storage is a SQLAlchemy engine pointed at
PostgreSQL in production, defaulting to a local SQLite file (`DATABASE_URL`)
so the API and tests need no external database:

```bash
pip install -r requirements.txt
uvicorn ai_rfp_generator.app:app --reload --app-dir src
# then: curl -F text="Build a customer portal" http://localhost:8000/requirements
```

`POST /requirements/{id}/outline` generates a document outline from the
requirement's normalized items via one OpenAI chat-completion call (model
configurable via `OUTLINE_MODEL`, defaulting to `gpt-4o-mini` — never
hardcoded, per the provider-swappable convention in `docs/architecture.md`),
validates the response (non-empty, ordered `{title, description}` sections),
and persists it linked to the source requirement. Returns `503` if
`OPENAI_API_KEY` isn't set. `examples/generate_outline_example.py` runs the
same normalize → generate flow end-to-end offline against a fake client, no
API key required:

```bash
PYTHONPATH=src python examples/generate_outline_example.py
```

Phase 2 begins with `POST /requirements/{id}/source-materials`, which accepts
one or more source documents (past proposals, case studies, capability
statements — `.txt`/`.docx`/`.pdf`, same multipart file upload convention as
`/requirements`) and stores them linked to the owning requirement. Files are
content-addressed on disk (sha256 of the bytes) under `SOURCE_MATERIALS_DIR`
(defaulting to a local `./source_materials/` folder, same env-var-with-local-
default convention as `DATABASE_URL`), so re-uploading identical content is a
no-op rather than a duplicate. The whole batch is rejected (400) if any file
is empty or an unsupported type, so a request never leaves a partial set of
materials stored. Extracted text is persisted alongside each file for the
fact-extraction step to draw cited claims from.

## 10. Evaluation

Document evaluation metrics and how to reproduce them here (see `docs/evaluation.md`).

## 11. Results

_To be filled in as the implementation progresses — screenshots, metrics tables, and
sample outputs go here._

## 12. API

_If this project exposes an API, document the main endpoints here (or link to
auto-generated OpenAPI docs, e.g. `/docs` for FastAPI)._

## 13. Docker

```bash
docker build -t ai-rfp-generator .
docker run -p 8000:8000 ai-rfp-generator
```

## 14. Tests

```bash
pytest tests/
```

### Versioned prompt catalog

Active prompt versions are configured in `configs/prompts/active.json`. Each prompt lives in its own versioned JSON file, for example:

```text
configs/prompts/
├── active.json
├── outline-v1.json
├── drafting-v1.json
└── validation-v1.json
```

To add a new prompt version:

1. Copy the existing stage prompt to a new versioned file such as `outline-v2.json`.
2. Change its `id`, `version`, and prompt text.
3. Point the relevant stage in `active.json` to the new ID.
4. Add/update a case in `configs/prompt_test_cases.json`.
5. Run the regression suite before committing.

The application loads the active outline and drafting prompts at runtime. `PROMPT_CONFIG_DIR` can point to another prompt directory for experiments or deployment-specific configuration without changing application code. Citation validation remains deterministic; `validation-v1.json` is available for optional semantic validation experiments and regression tests.

### Prompt regression tests

Prompt behavior is regression-tested offline with fake model clients, so CI does not require API credentials or spend model tokens:

```bash
PYTHONPATH=src python scripts/run_prompt_tests.py
```

Test cases live in `configs/prompt_test_cases.json`. Each case records the pipeline stage, prompt/version identifier, deterministic input/fake output, and expected structural checks.

## 15. Limitations

- This is a from-scratch, independent recreation built for portfolio purposes.
- Performance numbers, once added, are based on public datasets and are not
  representative of any production system's real-world results.

## 16. Future Work

- Expand evaluation coverage and add CI-based regression checks.
- Add more configuration presets and deployment targets.
- Track open items as GitHub Issues.

## 17. Disclosure

This repository is an **independent open-source recreation inspired by the kind of
production systems I have worked on professionally**. It contains no employer or
client source code, prompts, datasets, credentials, architecture diagrams, or
business logic. All code, data, and documentation here are original or built on
publicly available datasets and open-source tools.

---
_Last updated: 2026-08-18_
