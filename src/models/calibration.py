"""Confidence calibration — the contingency PRD §8 names explicitly:
"recalibration (e.g., temperature scaling or isotonic regression) is
budgeted as contingency work if [the calibration check] fails." It failed:
storage/results_validation_gemini.json's confidence_calibration block showed
the 80-100% stated-confidence band was only 57.1% actually correct, a
35.4-point gap against the NFR-03 "within 5 points" governance condition.

This fits an isotonic regression mapping RAW stated confidence -> CALIBRATED
confidence, from (confidence, was_correct) pairs collected ONLY from the
development set via src/models/fit_calibration.py — never from validation or
the hidden set, per Evaluation_Framework.md: "Develop against the sample
tickets... go to the test set once." The fitted dataset is persisted as raw
pairs, not a serialized sklearn model, so refitting at load time is cheap
and carries no model-format version risk across sklearn upgrades.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_CALIBRATION_DATASET_PATH = Path("storage/calibration_dataset.json")

_cached_model: Any = None
_cached_mtime: float | None = None
_cached_path: str | None = None


def _fit_model(path: Path):
    from sklearn.isotonic import IsotonicRegression

    pairs = json.loads(path.read_text(encoding="utf-8"))
    if len(pairs) < 10:
        # Too few points for a meaningful monotonic fit; treat as
        # unconfigured rather than fitting noise.
        return None
    x = [p["confidence"] for p in pairs]
    y = [1.0 if p["correct"] else 0.0 for p in pairs]
    model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    model.fit(x, y)
    return model


def get_calibrator(path: str | Path = DEFAULT_CALIBRATION_DATASET_PATH):
    """Returns a fitted isotonic model, or None if no calibration dataset
    has been fitted yet (src/models/fit_calibration.py hasn't been run), or
    it has too few points. Callers MUST treat None as "use the raw,
    uncalibrated confidence as-is" — never as an error, since calibration is
    an optional refinement, not a required dependency (CLAUDE.md's
    Unattended Gate Rule still applies here)."""
    global _cached_model, _cached_mtime, _cached_path
    p = Path(path)
    if not p.exists():
        return None
    mtime = p.stat().st_mtime
    if _cached_model is None or _cached_mtime != mtime or _cached_path != str(p):
        _cached_model = _fit_model(p)
        _cached_mtime = mtime
        _cached_path = str(p)
    return _cached_model


def calibrate(raw_confidence: float, path: str | Path = DEFAULT_CALIBRATION_DATASET_PATH) -> float:
    model = get_calibrator(path)
    if model is None:
        return raw_confidence
    return round(float(model.predict([raw_confidence])[0]), 4)
