"""Extensible stage declarations and callable executor registry.

Executors are deliberately unaware of publication and validation state. They
write into a private staging directory and return JSON metadata; the pipeline
owns hashes, manifests, cache decisions, and atomic publication.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ProjectConfig

IMPLEMENTED = "implemented"
EXPERIMENTAL = "experimental"
PLANNED = "planned"
BLOCKED = "blocked"
ALLOWED_STATUSES = {IMPLEMENTED, EXPERIMENTAL, PLANNED, BLOCKED}


@dataclass(frozen=True)
class Step:
    id: str
    status: str
    dependencies: tuple[str, ...]
    output: str
    description: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    external_inputs: tuple[str, ...] = ()
    cacheable: bool = True
    optional_external_inputs: tuple[str, ...] = ()
    capabilities_missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepContext:
    config: ProjectConfig
    output_dir: Path
    dependency_outputs: Mapping[str, Path]
    external_inputs: Mapping[str, Path]
    allow_network: bool
    allow_experimental: bool
    resolve_secret: Callable[[str], bytes] | None = None
    hash_file: Callable[[Path], str] | None = None


StepExecutor = Callable[[StepContext], dict[str, Any]]
_STEPS: dict[str, Step] = {}
_EXECUTORS: dict[str, StepExecutor] = {}


def _safe_relative(value: str, what: str) -> None:
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts or str(path) in {".", ""}:
        raise ValueError(f"{what} must be a safe relative path: {value!r}")


def register_step(step: Step, executor: StepExecutor | None = None) -> None:
    """Register a stage and optional callable executor before planning/running.

    A step without an executor is descriptive only and cannot run. A plugin
    should register its dependencies before invoking the pipeline. The engine
    rejects planned/blocked statuses before any filesystem mutation.
    """
    if not step.id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in step.id):
        raise ValueError(f"Invalid step id: {step.id!r}")
    if step.status not in ALLOWED_STATUSES:
        raise ValueError(f"Invalid implementation status: {step.status}")
    _safe_relative(step.output, "step.output")
    for output in step.outputs:
        _safe_relative(output, "step.outputs entry")
    for input_ref in (*step.external_inputs, *step.optional_external_inputs):
        if not input_ref or any(not part.isidentifier() for part in input_ref.split(".")):
            raise ValueError(f"external_inputs must reference a dotted config field: {input_ref!r}")
    for dependency in step.dependencies:
        if dependency == step.id:
            raise ValueError("A step cannot depend on itself")
    if step.id in _STEPS:
        raise ValueError(f"Step already registered: {step.id}")
    _STEPS[step.id] = step
    if executor is not None:
        _EXECUTORS[step.id] = executor


def replace_step(step: Step, executor: StepExecutor) -> None:
    """Replace a built-in declaration with its profile-aware implementation.

    This explicit override point lets adapter modules supply concrete stage
    declarations without editing the generic pipeline. It is intentionally
    unavailable for adding a new ID; use ``register_step`` for extensions.
    """
    if step.id not in _STEPS:
        raise ValueError(f"Cannot replace an unregistered step: {step.id}")
    if not callable(executor):
        raise TypeError("Step executor must be callable")
    # Reuse the declaration checks while preserving the existing registry key.
    existing = _STEPS.pop(step.id)
    _EXECUTORS.pop(step.id, None)
    try:
        register_step(step, executor)
    except Exception:
        _STEPS[step.id] = existing
        raise


def get_step(step_id: str) -> Step | None:
    return _STEPS.get(step_id)


def get_executor(step_id: str) -> StepExecutor | None:
    return _EXECUTORS.get(step_id)


def all_steps() -> tuple[Step, ...]:
    return tuple(_STEPS.values())
