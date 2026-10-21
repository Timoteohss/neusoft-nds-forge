#!/usr/bin/env python3
"""Reproducible synthetic benchmark for the experimental OSM clipping adapter.

The parent process writes a deterministic local PBF; a fresh child process clips
it so reported peak RSS covers clipping rather than fixture creation.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import time


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _fixture(path: Path, ways_count: int) -> None:
    import osmium

    writer = osmium.SimpleWriter(str(path))
    # Deterministic pseudo-uniform points spanning the polygon and its exterior.
    for index in range(ways_count * 2):
        x = (((index * 7919) % 20001) / 10000.0) - 1.0
        y = (((index * 104729) % 20001) / 10000.0) - 1.0
        writer.add_node(osmium.osm.mutable.Node(
            id=index + 1,
            location=(x, y),
            version=1,
            tags={"amenity": "cafe"},
        ))
    for way_index in range(ways_count):
        writer.add_way(osmium.osm.mutable.Way(
            id=way_index + 1,
            nodes=[way_index * 2 + 1, way_index * 2 + 2],
            version=1,
            tags={"highway": "residential", "name": f"Synthetic {way_index}"},
        ))
    writer.close()


def _clip(pbf: Path, output: Path, ways_count: int) -> dict:
    import shapely
    from shapely.geometry import Polygon, mapping

    from nds_forge.adapters.osm_extract import clip_pbf
    from nds_forge.contracts import RegionSpec, StageContext

    # A detailed polygon makes repeated point predicates representative of a
    # complex regional boundary while keeping the fixture small and generated.
    import math
    vertices = 1024
    ring = [(0.8 * math.cos(2 * math.pi * i / vertices),
             0.8 * math.sin(2 * math.pi * i / vertices))
            for i in range(vertices)]
    polygon = mapping(Polygon(ring))
    region = RegionSpec("synthetic-clip-benchmark", "synthetic clip benchmark",
                        None, polygon, True)
    context = StageContext(source_path=pbf, region=region, output_dir=output,
                           license="ODbL-1.0",
                           attribution="© OpenStreetMap contributors (synthetic benchmark)")
    started = time.perf_counter()
    report = clip_pbf(context)
    dataset = json.loads((output / "osm-dataset.json").read_text(encoding="utf-8"))
    # Temp paths differ between baseline and optimized invocations; exclude
    # that provenance locator while preserving every data/lineage field.
    dataset.get("provenance", {}).pop("path", None)
    semantic_dataset_hash = hashlib.sha256(json.dumps(
        dataset, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    return {
        "duration_seconds": time.perf_counter() - started,
        "peak_rss_bytes": _peak_rss_bytes(),
        "ways_requested": ways_count,
        "ways_clipped": report["counts"]["ways_intersecting_region"],
        "poi_nodes_collected": report["counts"]["nodes_collected"],
        "outputs_bytes": sum(item["bytes"] for item in report["outputs"].values()),
        "outputs_sha256": {name: item["sha256"]
                           for name, item in report["outputs"].items()},
        "dataset_semantic_sha256_without_temp_path": semantic_dataset_hash,
        "shapely_version": shapely.__version__,
        "osmium_version": importlib.metadata.version("osmium"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ways", type=int, default=5000)
    parser.add_argument("--repeat", type=int, default=1,
                        help="number of fresh child-process clipping runs")
    parser.add_argument("--repo-root", type=Path,
                        help="checkout containing the clipping implementation")
    parser.add_argument("--clip-pbf", type=Path,
                        help="clip an existing generated PBF in this process")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.ways < 1 or args.repeat < 1:
        parser.error("--ways and --repeat must be positive")

    if args.clip_pbf:
        if args.output is None:
            parser.error("--output is required with --clip-pbf")
        result = _clip(args.clip_pbf.resolve(), args.output.resolve(), args.ways)
        print(json.dumps(result, sort_keys=True))
        return 0

    repo_root = (args.repo_root or Path(__file__).resolve().parents[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="nds-clip-benchmark-") as temp_name:
        temp = Path(temp_name)
        pbf = temp / "synthetic.osm.pbf"
        _fixture(pbf, args.ways)
        pbf_hash = hashlib.sha256(pbf.read_bytes()).hexdigest()
        runs = []
        for repetition in range(args.repeat):
            output = temp / f"clip-output-{repetition}"
            child = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--clip-pbf", str(pbf),
                 "--output", str(output), "--ways", str(args.ways)],
                cwd=repo_root, env={**os.environ, "PYTHONPATH": str(repo_root)},
                text=True, capture_output=True, check=True,
            )
            runs.append(json.loads(child.stdout))
        print(json.dumps({
            "fixture": "deterministic synthetic OSM ways and amenity nodes",
            "ways_requested": args.ways,
            "pbf_bytes": pbf.stat().st_size,
            "pbf_sha256": pbf_hash,
            "runs": runs,
        }, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
