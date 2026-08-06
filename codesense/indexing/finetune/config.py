"""Configuration for the three finetune demo profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FINETUNE_PROFILES = ("lightweight", "full_force", "warn_full")


@dataclass(frozen=True, slots=True)
class FinetuneConfig:
    profile: str = "lightweight"
    model_path: Path | None = None
    artifact_dir: Path | None = None
    memory_budget_mb: int = 2048
    epochs: int = 2
    workers: int = 8
    allow_unsafe_full: bool = False
    preserve_full_model: bool = False
    strict_profile: bool = False

    def __post_init__(self) -> None:
        if self.profile not in FINETUNE_PROFILES:
            raise ValueError(f"unknown finetune profile: {self.profile}")
        if self.model_path is None:
            raise ValueError("finetune needs model_path")
        if self.memory_budget_mb <= 0:
            raise ValueError("memory_budget_mb must be positive")
        if self.profile == "warn_full" and not self.allow_unsafe_full:
            raise ValueError("warn_full needs allow_unsafe_full=True")
        if self.preserve_full_model and self.profile != "warn_full":
            raise ValueError("preserve_full_model is only valid for warn_full")
