# CLAUDE.md — FDE Capstone Operating Rules

## 1. Zero Invention Policy
- Never invent requirements, schemas, metrics, external APIs, or architectural components not grounded in `_context_md/` and `Capstone_Pack/05_Datasets/`.
- Before writing any code for a component, read `_context_md/02_Build_Specification.md`, `_context_md/Setup_Guide.md`, `_context_md/Evaluation_Framework.md`, and `_context_md/Governance_Framework.md`.

## 2. The Unattended Gate Rule (CRITICAL)
- The evaluation harness MUST accept `--input <path>` and `--output <path>` via CLI.
- NEVER hardcode `development_tickets.json` or `validation_tickets.json` inside pipeline or evaluation logic.
- The pipeline must handle API timeouts, malformed LLM JSON, and rate limits gracefully so a 120-ticket unattended run never crashes mid-batch.

## 3. Traceability & Governance
- Every prompt must live in a versioned file (`triage_v2.yaml`, etc.) and map to a `REQ-ID` from `PRD_v1.md` and `Stage_3_Prompt_Library.md`.
- Every ticket processed must write a complete record to the Decision Log DB matching the exact schema in `Governance_Framework`.