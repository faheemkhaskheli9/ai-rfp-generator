# Architecture Notes: RFP Generation using LLM

## Pipeline

```text
Requirement -> Document Structure -> Fact Extraction -> Section Generation -> Validation -> Final RFP Export
```

## Components

- Requirement intake
- Document structure planning
- Fact extraction from source materials
- Section-by-section generation
- Validation pass
- Multiple generation-strategy support
- Prompt configuration and experimentation
- Automatic evaluation
- Export to DOCX/PDF

## Design Notes

- Keep provider/model choices swappable behind interfaces (see `multi-llm-router`
  and similar projects in this portfolio for the general pattern).
- Prefer configuration-driven pipelines (YAML/JSON in `configs/`) over hardcoded
  parameters so experiments are reproducible.
