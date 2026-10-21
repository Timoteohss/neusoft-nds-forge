from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema_validation import validate_json_schema


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectConfig:
    path: Path
    raw: dict[str, Any]
    project_id: str
    workspace: Path
    region: dict[str, Any]
    source: dict[str, Any]
    pipeline: dict[str, Any]
    neusoft: dict[str, Any]
    schema_validated: bool = False

    def fingerprint_data(self) -> dict[str, Any]:
        value = dict(self.raw)
        value["workspace"] = str(self.workspace)
        return value


def load_config(path: Path) -> ProjectConfig:
    path = path.resolve()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Não foi possível ler TOML {path}: {exc}") from exc
    if raw.get("schema_version") != 1:
        raise ConfigError("schema_version deve ser 1")
    for name in ("project_id", "region", "source", "pipeline"):
        if name not in raw:
            raise ConfigError(f"Campo obrigatório ausente: {name}")
    project_id = raw["project_id"]
    if not isinstance(project_id, str) or not project_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in project_id):
        raise ConfigError("project_id aceita somente letras, números, hífen e underscore")
    workspace = Path(raw.get("workspace", "./work"))
    if not workspace.is_absolute():
        workspace = (path.parent / workspace).resolve()
    region = raw["region"]
    source = raw["source"]
    pipeline = raw["pipeline"]
    if region.get("source") not in {"bbox", "boundary_geojson", "relation"}:
        raise ConfigError("region.source deve ser bbox, boundary_geojson ou relation")
    if region["source"] == "bbox":
        bbox = region.get("bbox")
        if (not isinstance(bbox, list) or len(bbox) != 4 or
                not all(isinstance(x, (int, float)) for x in bbox)):
            raise ConfigError("region.bbox deve conter [oeste,sul,leste,norte]")
        west, south, east, north = bbox
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ConfigError("region.bbox fora de limites ou invertida")
    if region["source"] == "boundary_geojson" and not region.get("boundary_geojson"):
        raise ConfigError("boundary_geojson é obrigatório")
    if region["source"] == "relation" and not isinstance(region.get("osm_relation_id"), int):
        raise ConfigError("osm_relation_id inteiro é obrigatório para region.source=relation")
    if source.get("kind") != "pbf":
        raise ConfigError("A fonte inicial suportada é kind=pbf")
    if not source.get("path") and not source.get("url"):
        raise ConfigError("Informe source.path ou source.url")
    expected = source.get("sha256", "")
    if expected and (len(expected) != 64 or any(c not in "0123456789abcdefABCDEF" for c in expected)):
        raise ConfigError("source.sha256 deve ser SHA-256 hexadecimal")
    schema_path = Path(__file__).resolve().parent.parent / "schemas" / "project.schema.json"
    try:
        schema_validated = validate_json_schema(raw, schema_path)
    except Exception as exc:
        raise ConfigError(f"Project schema validation failed: {exc}") from exc
    return ProjectConfig(path, raw, project_id, workspace, region, source,
                         pipeline, raw.get("neusoft", {}), schema_validated)


def canonical_hash(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(data).hexdigest()
