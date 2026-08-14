"""Model artifacts, with enough provenance to reproduce or distrust them.

A pickle with no metadata is not a model, it is a liability: nothing in it
records which features it expects, which weeks it was fitted on, or which
commit produced it, so a stale artifact loaded against changed feature code
returns confident nonsense and no error.

What is stored, and why each one earns its place:

  model.pkl          the fitted estimator. Requires scikit-learn to load.
  linear_spec.json   the servable form of a linear model -- plain floats,
                     scoreable without numpy or scikit-learn. Written whenever
                     the selected model is linear, because the deployed API
                     runs under a 250 MB serverless limit and the scientific
                     stack is 332.9 MB.
  serving.csv        precomputed feature rows for the servable weeks, so the
                     API does not need the 36.8M-row causal scan at request
                     time and returns the same number on every call.
  metadata.json      everything below.

THE VERSION CHECK IS LOAD-BEARING

load() refuses an artifact whose feature_version or contract_version differs
from the running code. The failure it prevents is silent: adding a feature
shifts every column index, the stored coefficients still multiply cleanly
against the new matrix, and the model returns a plausible wrong number with no
exception anywhere. Refusing to load is the only outcome that surfaces it.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rrip.config import PROJECT_ROOT
from rrip.forecast import contract as C

logger = logging.getLogger(__name__)

MODEL_DIR = PROJECT_ROOT / "models" / "forecast"

MODEL_FILE = "model.pkl"
LINEAR_FILE = "linear_spec.json"
SERVING_FILE = "serving.csv"
METADATA_FILE = "metadata.json"


def git_sha() -> str:
    """The commit the artifact was built from, or a marker that it is dirty.

    A clean SHA is a promise that the code producing this model is in history.
    An artifact built from uncommitted work says so -- '<sha>-dirty' -- rather
    than claiming a commit whose contents differ from what actually ran.
    """
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, capture_output=True,
            text=True, timeout=10, check=True).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=PROJECT_ROOT,
            capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        return f"{sha}-dirty" if dirty else sha
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        return "unknown"


@dataclass
class ModelMetadata:
    """Everything needed to reproduce, audit or reject this artifact."""

    model_type: str
    hyperparameters: dict
    random_seed: int
    trained_at: str
    git_sha: str
    contract_version: str
    feature_version: str
    contract: dict
    dataset: dict
    splits: dict
    department_stats: dict
    selection: dict
    metrics: dict
    conformal: dict
    importance: list[dict]
    reference_values: dict
    # Which predictor won the promotion rule, with the evidence. When the
    # baseline wins, `model_type` above names the baseline and `challenger`
    # holds the fitted model that did not earn deployment -- both are kept, so
    # the decision stays auditable instead of becoming a deleted branch.
    deployment: dict = field(default_factory=dict)
    challenger: dict = field(default_factory=dict)
    leakage_audit: dict = field(default_factory=dict)
    robustness: dict = field(default_factory=dict)
    servable_weeks: list[int] = field(default_factory=list)
    departments: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model_type": self.model_type,
            "hyperparameters": self.hyperparameters,
            "random_seed": self.random_seed,
            "trained_at": self.trained_at,
            "git_sha": self.git_sha,
            "contract_version": self.contract_version,
            "feature_version": self.feature_version,
            "contract": self.contract,
            "dataset": self.dataset,
            "splits": self.splits,
            "department_stats": self.department_stats,
            "selection": self.selection,
            "metrics": self.metrics,
            "conformal": self.conformal,
            "importance": self.importance,
            "reference_values": self.reference_values,
            "deployment": self.deployment,
            "challenger": self.challenger,
            "leakage_audit": self.leakage_audit,
            "robustness": self.robustness,
            "servable_weeks": self.servable_weeks,
            "departments": self.departments,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ModelMetadata:
        return cls(**{k: d[k] for k in (
            "model_type", "hyperparameters", "random_seed", "trained_at",
            "git_sha", "contract_version", "feature_version", "contract",
            "dataset", "splits", "department_stats", "selection", "metrics",
            "conformal", "importance", "reference_values")},
            deployment=d.get("deployment", {}),
            challenger=d.get("challenger", {}),
            leakage_audit=d.get("leakage_audit", {}),
            robustness=d.get("robustness", {}),
            servable_weeks=d.get("servable_weeks", []),
            departments=d.get("departments", []),
            notes=d.get("notes", []))

    @property
    def version(self) -> str:
        """The string the API returns as model_version.

        Contract version, model type and the build's commit -- enough for a
        user reading a forecast to say exactly which artifact produced it.
        """
        short = self.git_sha[:8] if self.git_sha != "unknown" else "nogit"
        return f"{self.contract_version}/{self.model_type}/{short}"


class ArtifactError(RuntimeError):
    pass


@dataclass
class Artifact:
    metadata: ModelMetadata
    model: Any = None
    linear_spec: dict | None = None
    serving: Any = None          # pandas DataFrame, loaded lazily


def save(directory: Path, model: Any, metadata: ModelMetadata,
         serving_frame: Any, linear_spec: dict | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)

    import joblib

    joblib.dump(model, directory / MODEL_FILE)
    serving_frame.to_csv(directory / SERVING_FILE, index=False)

    if linear_spec is not None:
        (directory / LINEAR_FILE).write_text(
            json.dumps(linear_spec, indent=2), encoding="utf-8")
    elif (directory / LINEAR_FILE).exists():
        # A previous build may have been linear. Leaving its spec behind would
        # let the dependency-free path serve a model that is no longer the one
        # the metadata describes.
        (directory / LINEAR_FILE).unlink()

    (directory / METADATA_FILE).write_text(
        json.dumps(metadata.to_dict(), indent=2, default=str), encoding="utf-8")

    logger.info("forecast artifact written to %s (%s)", directory,
                metadata.version)
    return directory


def load_metadata(directory: Path = MODEL_DIR) -> ModelMetadata:
    path = directory / METADATA_FILE
    if not path.exists():
        raise ArtifactError(
            f"no forecast model at {directory}. Build one with `rrip "
            "forecast-train`.")
    meta = ModelMetadata.from_dict(json.loads(path.read_text(encoding="utf-8")))

    if meta.contract_version != C.CONTRACT_VERSION:
        raise ArtifactError(
            f"artifact contract {meta.contract_version!r} does not match the "
            f"running contract {C.CONTRACT_VERSION!r}. Retrain rather than "
            "serving a model whose target definition has changed.")
    if meta.feature_version != C.FEATURE_VERSION:
        raise ArtifactError(
            f"artifact feature version {meta.feature_version!r} does not match "
            f"the running code {C.FEATURE_VERSION!r}. The stored coefficients "
            "would be applied to a different set of columns and would still "
            "produce a number.")
    return meta


def load(directory: Path = MODEL_DIR, with_model: bool = True) -> Artifact:
    """Load an artifact, verifying it matches the running contract."""
    meta = load_metadata(directory)

    art = Artifact(metadata=meta)

    spec_path = directory / LINEAR_FILE
    if spec_path.exists():
        art.linear_spec = json.loads(spec_path.read_text(encoding="utf-8"))

    serving_path = directory / SERVING_FILE
    if serving_path.exists():
        import pandas as pd
        art.serving = pd.read_csv(serving_path)

    if with_model:
        model_path = directory / MODEL_FILE
        if not model_path.exists():
            raise ArtifactError(f"{MODEL_FILE} missing from {directory}")
        import joblib
        art.model = joblib.load(model_path)

    return art


def build_metadata(model_type: str, hyperparameters: dict, seed: int,
                   **blocks: Any) -> ModelMetadata:
    return ModelMetadata(
        model_type=model_type,
        hyperparameters=hyperparameters,
        random_seed=seed,
        trained_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_sha=git_sha(),
        contract_version=C.CONTRACT_VERSION,
        feature_version=C.FEATURE_VERSION,
        contract=C.CONTRACT.to_dict(),
        **blocks)
