"""Synthetic plugin example; it does not read an OSM dataset or build a map."""
from __future__ import annotations

import json

from nds_forge.registry import EXPERIMENTAL, Step, StepContext, register_step


def _write_fixture(context: StepContext) -> dict:
    source_path = context.dependency_outputs["source.acquire"] / "source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    payload = {
        "kind": "synthetic_fixture_only",
        "source_sha256": source["sha256"],
        "capabilities_missing": ["OSM parse", "geometry", "routing", "Neusoft package"],
    }
    (context.output_dir / "fixture.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"kind": payload["kind"], "fixture_sha256": source["sha256"]}


def register() -> None:
    register_step(
        Step(
            id="fixture.synthetic",
            status=EXPERIMENTAL,
            dependencies=("source.acquire",),
            output="fixture-synthetic",
            description="Demonstrar extensão do DAG com metadados sintéticos",
            inputs=("source.acquire/source.json",),
            outputs=("fixture.json",),
            capabilities=("synthetic_fixture",),
        ),
        _write_fixture,
    )
