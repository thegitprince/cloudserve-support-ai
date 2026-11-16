"""SQLite-backed Decision Log and administrative kill switch.

Implements FR-09 (every ticket processed writes a complete record matching
the schema in _context_md/Governance_Framework.md) and FR-10 (an
administrative kill switch that halts automated response generation
immediately, without a deployment, and is itself logged).

The record schema matches Governance_Framework.md's minimum record exactly:

    {
      "decision_id": "...",
      "timestamp": "...",
      "ticket_id": "...",
      "stage": "classification | routing | generation | validation",
      "input_summary": "...",
      "model": {"name": "...", "version": "..."},
      "prediction": {"value": "...", "confidence": 0.00},
      "alternatives": [{"value": "...", "confidence": 0.00}],
      "sources_used": [{"doc_id": "...", "score": 0.00}],
      "threshold_applied": 0.00,
      "action_taken": "auto_respond | escalate | block",
      "reason": "human readable explanation of why this action followed",
      "guardrail_results": {"pii": "pass", "grounding": "pass", "tone": "pass"},
      "prompt_version": "PR-02 v1.3",
      "requirement_ids": ["FR-03", "FR-07"]
    }

Coverage check (Governance_Framework.md §1): logged decisions must reconcile
exactly against tickets processed. That reconciliation is the evaluation
harness's job (src/evaluate.py), not this module's — this module only
guarantees that every write it is asked to make either succeeds or raises,
never silently drops a record.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

Stage = Literal["classification", "routing", "generation", "validation"]
ActionTaken = Literal["auto_respond", "escalate", "block"]

DEFAULT_DB_PATH = Path("storage/decisions.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id       TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    ticket_id         TEXT NOT NULL,
    stage             TEXT NOT NULL,
    input_summary     TEXT,
    model_json        TEXT NOT NULL,
    prediction_json   TEXT NOT NULL,
    alternatives_json TEXT NOT NULL,
    sources_used_json TEXT NOT NULL,
    threshold_applied REAL,
    action_taken      TEXT NOT NULL,
    reason            TEXT NOT NULL,
    guardrail_results_json TEXT NOT NULL,
    prompt_version    TEXT,
    requirement_ids_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_decisions_ticket_id ON decisions (ticket_id);
CREATE INDEX IF NOT EXISTS idx_decisions_stage ON decisions (stage);

CREATE TABLE IF NOT EXISTS kill_switch_state (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    enabled     INTEGER NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kill_switch_audit (
    audit_id    TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    operator    TEXT NOT NULL,
    action      TEXT NOT NULL,
    reason      TEXT NOT NULL
);
"""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Decision:
    """One row of the Decision Log, matching Governance_Framework.md's schema."""

    ticket_id: str
    stage: Stage
    action_taken: ActionTaken
    reason: str
    model_name: str
    model_version: str
    prediction_value: Any
    prediction_confidence: float
    input_summary: str = ""
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    sources_used: list[dict[str, Any]] = field(default_factory=list)
    threshold_applied: float | None = None
    guardrail_results: dict[str, str] = field(default_factory=dict)
    prompt_version: str | None = None
    requirement_ids: list[str] = field(default_factory=list)
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=_utcnow_iso)

    def to_record(self) -> dict[str, Any]:
        """The exact JSON shape named in Governance_Framework.md."""
        return {
            "decision_id": self.decision_id,
            "timestamp": self.timestamp,
            "ticket_id": self.ticket_id,
            "stage": self.stage,
            "input_summary": self.input_summary,
            "model": {"name": self.model_name, "version": self.model_version},
            "prediction": {
                "value": self.prediction_value,
                "confidence": self.prediction_confidence,
            },
            "alternatives": self.alternatives,
            "sources_used": self.sources_used,
            "threshold_applied": self.threshold_applied,
            "action_taken": self.action_taken,
            "reason": self.reason,
            "guardrail_results": self.guardrail_results,
            "prompt_version": self.prompt_version,
            "requirement_ids": self.requirement_ids,
        }


class DecisionLog:
    """SQLite-backed store for the Decision Log and the kill switch state.

    Usage:
        log = DecisionLog()           # uses storage/decisions.db
        log.record(Decision(...))
        log.is_kill_switch_enabled()  # False means the system is halted
        log.set_kill_switch(enabled=False, operator="prince", reason="...")
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._ensure_kill_switch_row()
        self._conn.commit()

    def _ensure_kill_switch_row(self) -> None:
        row = self._conn.execute(
            "SELECT id FROM kill_switch_state WHERE id = 1"
        ).fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO kill_switch_state (id, enabled, updated_at) VALUES (1, 1, ?)",
                (_utcnow_iso(),),
            )

    # --- Decision Log (FR-09) -------------------------------------------

    def record(self, decision: Decision) -> str:
        """Write one decision record. Raises on failure rather than
        swallowing the error, because a silently dropped decision is the
        exact failure mode the Governance Framework's coverage check exists
        to catch."""
        self._conn.execute(
            """
            INSERT INTO decisions (
                decision_id, created_at, ticket_id, stage, input_summary,
                model_json, prediction_json, alternatives_json,
                sources_used_json, threshold_applied, action_taken, reason,
                guardrail_results_json, prompt_version, requirement_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision.decision_id,
                decision.timestamp,
                decision.ticket_id,
                decision.stage,
                decision.input_summary,
                json.dumps({"name": decision.model_name, "version": decision.model_version}),
                json.dumps(
                    {
                        "value": decision.prediction_value,
                        "confidence": decision.prediction_confidence,
                    }
                ),
                json.dumps(decision.alternatives),
                json.dumps(decision.sources_used),
                decision.threshold_applied,
                decision.action_taken,
                decision.reason,
                json.dumps(decision.guardrail_results),
                decision.prompt_version,
                json.dumps(decision.requirement_ids),
            ),
        )
        self._conn.commit()
        return decision.decision_id

    def count_for_ticket(self, ticket_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        return row[0]

    def total_decisions(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM decisions").fetchone()
        return row[0]

    def distinct_tickets_logged(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT ticket_id) FROM decisions"
        ).fetchone()
        return row[0]

    def distinct_tickets_logged_among(self, ticket_ids: list[str]) -> int:
        """Like distinct_tickets_logged, but scoped to a given set of
        ticket_ids. The coverage check (Governance_Framework.md §1, "count
        your logged decisions against your processed tickets") must compare
        against THIS run's tickets, not every row ever written to this
        database file across all prior runs — storage/decisions.db is a
        durable audit log by design (Governance_Framework.md §1: "reconstructable
        months later"), so it is expected to accumulate rows from earlier
        runs, and distinct_tickets_logged() alone is the wrong basis for a
        per-run reconciliation check."""
        if not ticket_ids:
            return 0
        placeholders = ",".join("?" for _ in ticket_ids)
        row = self._conn.execute(
            f"SELECT COUNT(DISTINCT ticket_id) FROM decisions WHERE ticket_id IN ({placeholders})",
            ticket_ids,
        ).fetchone()
        return row[0]

    def fetch_for_ticket(self, ticket_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM decisions WHERE ticket_id = ? ORDER BY created_at",
            (ticket_id,),
        ).fetchall()
        columns = [c[0] for c in self._conn.execute("SELECT * FROM decisions LIMIT 0").description]
        return [self._row_to_record(dict(zip(columns, r))) for r in rows]

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "decision_id": row["decision_id"],
            "timestamp": row["created_at"],
            "ticket_id": row["ticket_id"],
            "stage": row["stage"],
            "input_summary": row["input_summary"],
            "model": json.loads(row["model_json"]),
            "prediction": json.loads(row["prediction_json"]),
            "alternatives": json.loads(row["alternatives_json"]),
            "sources_used": json.loads(row["sources_used_json"]),
            "threshold_applied": row["threshold_applied"],
            "action_taken": row["action_taken"],
            "reason": row["reason"],
            "guardrail_results": json.loads(row["guardrail_results_json"]),
            "prompt_version": row["prompt_version"],
            "requirement_ids": json.loads(row["requirement_ids_json"]),
        }

    # --- Administrative kill switch (FR-10) -----------------------------

    def is_kill_switch_enabled(self) -> bool:
        """True means automated responses are ALLOWED (normal operation).
        False means the kill switch has been engaged and every ticket must
        route to escalation regardless of confidence, without any code
        change or redeploy."""
        row = self._conn.execute(
            "SELECT enabled FROM kill_switch_state WHERE id = 1"
        ).fetchone()
        return bool(row[0])

    def set_kill_switch(self, enabled: bool, operator: str, reason: str) -> str:
        """Toggle the kill switch and write an auditable record of the
        toggle itself — the toggle action is logged exactly like any other
        governance-relevant decision, per Governance_Framework.md §5."""
        now = _utcnow_iso()
        self._conn.execute(
            "UPDATE kill_switch_state SET enabled = ?, updated_at = ? WHERE id = 1",
            (1 if enabled else 0, now),
        )
        audit_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO kill_switch_audit (audit_id, created_at, operator, action, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (audit_id, now, operator, "enable" if enabled else "disable", reason),
        )
        self._conn.commit()
        return audit_id

    def kill_switch_audit_trail(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT audit_id, created_at, operator, action, reason "
            "FROM kill_switch_audit ORDER BY created_at"
        ).fetchall()
        return [
            {
                "audit_id": r[0],
                "created_at": r[1],
                "operator": r[2],
                "action": r[3],
                "reason": r[4],
            }
            for r in rows
        ]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "DecisionLog":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
