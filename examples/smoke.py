"""Exercise the CLI with a tiny non-PBF fixture; this does not parse OSM."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="nds-forge-smoke-") as name:
        root = Path(name)
        fixture = root / "synthetic-not-a-pbf.fixture"
        fixture.write_bytes(b"SYNTHETIC HASH FIXTURE; NOT AN OSM PBF\n")
        config = root / "project.toml"
        config.write_text(
            "\n".join([
                "schema_version = 1", 'project_id = "cli-smoke"', 'workspace = "./work"',
                "", "[region]", 'id = "fixture"', 'source = "bbox"',
                "bbox = [-1.0, -1.0, 1.0, 1.0]", "", "[source]", 'kind = "pbf"',
                f'path = "{fixture}"', 'license = "ODbL-1.0"',
                'attribution = "© OpenStreetMap contributors"', "", "[pipeline]",
                'target = "source.verify"', "",
            ]) + "\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "nds_forge", "run", "--config", str(config)],
            cwd=repo, check=True, capture_output=True, text=True, timeout=20)
        report = json.loads(result.stdout)
        print(json.dumps({"result": "pass", "target": report["target"],
                          "validation": report["overall_validation"],
                          "fixture": "plain test bytes, not valid PBF"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
