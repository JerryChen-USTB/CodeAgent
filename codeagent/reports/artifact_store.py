"""Artifact index persistence."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArtifactKind(str, Enum):
    REPORT = "report"
    PATCH = "patch"
    LOG = "log"
    JSON = "json"
    CODE = "code"
    CONFIG = "config"


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    artifact_id: str
    stage: str
    kind: ArtifactKind
    path: str | Path
    summary: str
    related_requirement_ids: list[str] = Field(default_factory=list)

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str | Path) -> str:
        return str(value).replace("\\", "/")


class ArtifactStore(BaseModel):
    run_id: str
    run_dir: Path
    artifacts: list[ArtifactRecord] = Field(default_factory=list)

    @classmethod
    def create(cls, run_dir: Path, *, run_id: str) -> "ArtifactStore":
        return cls(run_id=run_id, run_dir=run_dir, artifacts=[])

    @classmethod
    def load(cls, run_dir: Path) -> "ArtifactStore":
        path = run_dir / "artifacts_index.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            run_id=data["run_id"],
            run_dir=run_dir,
            artifacts=[ArtifactRecord.model_validate(item) for item in data["artifacts"]],
        )

    def record(self, record: ArtifactRecord) -> ArtifactRecord:
        normalized = record.model_copy(
            update={"path": _relative_artifact_path(self.run_dir, record.path)}
        )
        existing = self.find(normalized.artifact_id)
        if existing is not None:
            self.artifacts = [
                normalized if item.artifact_id == normalized.artifact_id else item
                for item in self.artifacts
            ]
        else:
            self.artifacts.append(normalized)
        return normalized

    def find(self, artifact_id: str) -> ArtifactRecord | None:
        return next(
            (artifact for artifact in self.artifacts if artifact.artifact_id == artifact_id),
            None,
        )

    def find_by_stage(self, stage: str) -> list[ArtifactRecord]:
        return [artifact for artifact in self.artifacts if artifact.stage == stage]

    def write(self) -> None:
        path = self.run_dir / "artifacts_index.json"
        payload = {
            "run_id": self.run_id,
            "artifacts": [
                artifact.model_dump(mode="json", exclude_none=True)
                for artifact in self.artifacts
            ],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _relative_artifact_path(run_dir: Path, path: str | Path) -> str:
    candidate = Path(path)
    resolved_run_dir = run_dir.resolve()
    if candidate.is_absolute():
        resolved_candidate = candidate.resolve()
        try:
            relative = resolved_candidate.relative_to(resolved_run_dir)
        except ValueError as exc:
            raise ValueError(
                f"artifact path must be inside run directory: {candidate}"
            ) from exc
        return relative.as_posix()

    if any(part == ".." for part in candidate.parts):
        raise ValueError(f"artifact path must not traverse outside run directory: {path}")
    if candidate.anchor:
        raise ValueError(f"artifact path must be relative to run directory: {path}")
    return candidate.as_posix()
