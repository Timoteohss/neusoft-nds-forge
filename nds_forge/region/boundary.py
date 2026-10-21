"""Boundary source validation, geometry integrity verification, and CRS enforcement.

Checks region boundary structure, WGS84 metadata, holes, and source provenance.
Topological validation is performed only when Shapely is available; metadata
distinguishes a successful topology check from the structural-only fallback.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_CRS = frozenset({
    "EPSG:4326",
    "urn:ogc:def:crs:OGC:1.3:CRS84",
    "urn:ogc:def:crs:EPSG::4326",
    "CRS84",
    "WGS84",
    "urn:ogc:def:crs:OGC:2:84",
})


class BoundaryError(ValueError):
    """Raised when a region boundary contains invalid geometry, unsupported CRS, or missing provenance."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ValidatedBoundary:
    """A structurally checked boundary with explicit topology evidence."""
    geometry: dict[str, Any]
    bbox: tuple[float, float, float, float]
    crs: str
    geom_type: str
    has_holes: bool
    holes_count: int
    is_valid: bool | None
    topology_checked: bool
    source_path: str | None = None
    sha256: str | None = None
    license: str | None = None
    attribution: str | None = None
    osm_relation_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "bbox": list(self.bbox),
            "crs": self.crs,
            "geom_type": self.geom_type,
            "geometry_geojson": self.geometry,
            "has_holes": self.has_holes,
            "holes_count": self.holes_count,
            "is_valid": self.is_valid,
            "topology_checked": self.topology_checked,
        }
        if self.source_path is not None:
            result["path"] = self.source_path
        if self.sha256 is not None:
            result["sha256"] = self.sha256
        if self.license is not None:
            result["license"] = self.license
        if self.attribution is not None:
            result["attribution"] = self.attribution
        if self.osm_relation_id is not None:
            result["osm_relation_id"] = self.osm_relation_id
        return dict(sorted(result.items()))


def validate_crs(crs_data: Any) -> str:
    """Validate CRS metadata and ensure WGS84 / EPSG:4326 conformity."""
    if crs_data is None:
        return "EPSG:4326"
    crs_name = None
    if isinstance(crs_data, str):
        crs_name = crs_data
    elif isinstance(crs_data, dict):
        properties = crs_data.get("properties")
        if isinstance(properties, dict):
            crs_name = properties.get("name")
        elif "type" in crs_data and crs_data["type"] == "name":
            crs_name = crs_data.get("name")
    if (not isinstance(crs_name, str) or
            crs_name.strip().casefold() not in {supported.casefold() for supported in SUPPORTED_CRS}):
        raise BoundaryError(
            f"CRS não suportado: {crs_name!r}. O pipeline exige exclusivamente WGS84 (EPSG:4326)."
        )
    return "EPSG:4326"


def _check_ring_coordinates(ring: Any, ring_name: str = "exterior") -> list[tuple[float, float]]:
    if not isinstance(ring, list) or len(ring) < 4:
        raise BoundaryError(f"Anel {ring_name} deve conter ao menos 4 coordenadas (fechado)")
    coords: list[tuple[float, float]] = []
    for pt in ring:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2 or not all(isinstance(c, (int, float)) for c in pt[:2]):
            raise BoundaryError(f"Ponto inválido no anel {ring_name}: {pt}")
        lon, lat = float(pt[0]), float(pt[1])
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            raise BoundaryError(f"Coordenadas fora dos limites geográficos [-180, 180], [-90, 90]: ({lon}, {lat})")
        coords.append((lon, lat))
    if coords[0] != coords[-1]:
        raise BoundaryError(f"Anel {ring_name} não está fechado: primeiro ponto {coords[0]} != último {coords[-1]}")
    return coords


def validate_boundary_geometry(geometry: dict[str, Any], crs: str = "EPSG:4326") -> ValidatedBoundary:
    """Check polygon structure and report whether optional topology validation ran."""
    if not isinstance(geometry, dict):
        raise BoundaryError("Geometria deve ser um objeto JSON")
    geom_type = geometry.get("type")
    if geom_type not in {"Polygon", "MultiPolygon"}:
        raise BoundaryError(f"Tipo de geometria não suportado: {geom_type!r}. Esperado Polygon ou MultiPolygon.")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        raise BoundaryError("Coordenadas ausentes ou vazias na geometria")

    all_points: list[tuple[float, float]] = []
    holes_count = 0

    if geom_type == "Polygon":
        rings = coordinates
        _check_ring_coordinates(rings[0], "exterior")
        all_points.extend((float(p[0]), float(p[1])) for p in rings[0])
        for idx, hole in enumerate(rings[1:], 1):
            _check_ring_coordinates(hole, f"interior_{idx}")
            all_points.extend((float(p[0]), float(p[1])) for p in hole)
            holes_count += 1
    elif geom_type == "MultiPolygon":
        for poly_idx, poly_rings in enumerate(coordinates):
            if not isinstance(poly_rings, list) or not poly_rings:
                raise BoundaryError(f"Polígono {poly_idx} vazio no MultiPolygon")
            _check_ring_coordinates(poly_rings[0], f"exterior_poly_{poly_idx}")
            all_points.extend((float(p[0]), float(p[1])) for p in poly_rings[0])
            for hole_idx, hole in enumerate(poly_rings[1:], 1):
                _check_ring_coordinates(hole, f"interior_poly_{poly_idx}_{hole_idx}")
                all_points.extend((float(p[0]), float(p[1])) for p in hole)
                holes_count += 1

    # Structural checks above do not establish topological validity.
    topology_checked = False
    try:
        from shapely.geometry import shape
        from shapely.validation import explain_validity
        shapely_geom = shape(geometry)
        if shapely_geom.is_empty:
            raise BoundaryError("Geometria vazia não é permitida como limite de região")
        if not shapely_geom.is_valid:
            reason = explain_validity(shapely_geom)
            raise BoundaryError(f"Geometria de limite inválida: {reason}")
        bounds = tuple(float(x) for x in shapely_geom.bounds)
        topology_checked = True
    except ImportError:
        min_x = min(p[0] for p in all_points)
        min_y = min(p[1] for p in all_points)
        max_x = max(p[0] for p in all_points)
        max_y = max(p[1] for p in all_points)
        bounds = (min_x, min_y, max_x, max_y)

    return ValidatedBoundary(
        geometry=geometry,
        bbox=(bounds[0], bounds[1], bounds[2], bounds[3]),
        crs=crs,
        geom_type=geom_type,
        has_holes=holes_count > 0,
        holes_count=holes_count,
        is_valid=True if topology_checked else None,
        topology_checked=topology_checked,
    )


def load_boundary_geojson(path: Path) -> ValidatedBoundary:
    """Load, verify, and validate a GeoJSON boundary file from disk."""
    if not path.is_file():
        raise BoundaryError(f"Arquivo boundary GeoJSON não encontrado: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BoundaryError(f"Arquivo GeoJSON inválido ou corrompido {path}: {exc}") from exc

    crs = validate_crs(doc.get("crs"))
    license_info = doc.get("licence") or doc.get("license")
    attribution = doc.get("attribution")

    geometry = None
    osm_relation_id = None

    if doc.get("type") == "FeatureCollection":
        features = doc.get("features", [])
        if not features or not isinstance(features, list):
            raise BoundaryError("FeatureCollection não contém features válidas")
        if len(features) != 1:
            raise BoundaryError(
                "FeatureCollection com múltiplas features não é suportada como limite; "
                "forneça uma Feature única com Polygon ou MultiPolygon explícito."
            )
        feature = features[0]
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise BoundaryError("FeatureCollection contém uma feature inválida")
        geometry = feature.get("geometry")
        properties = feature.get("properties", {})
        if not isinstance(properties, dict):
            raise BoundaryError("properties da feature devem ser um objeto JSON")
        if not license_info:
            license_info = properties.get("license") or properties.get("licence")
        if not attribution:
            attribution = properties.get("attribution")
        if properties.get("osm_type") == "relation" and properties.get("osm_id"):
            osm_relation_id = int(properties["osm_id"])
    elif doc.get("type") == "Feature":
        geometry = doc.get("geometry")
        properties = doc.get("properties", {})
        if not license_info:
            license_info = properties.get("license") or properties.get("licence")
        if properties.get("osm_type") == "relation" and properties.get("osm_id"):
            osm_relation_id = int(properties["osm_id"])
    elif doc.get("type") in {"Polygon", "MultiPolygon"}:
        geometry = doc
    else:
        raise BoundaryError(f"Estrutura GeoJSON raiz não reconhecida: {doc.get('type')}")

    if not geometry:
        raise BoundaryError("Nenhuma geometria encontrada no arquivo GeoJSON")

    validated = validate_boundary_geometry(geometry, crs=crs)

    return ValidatedBoundary(
        geometry=validated.geometry,
        bbox=validated.bbox,
        crs=validated.crs,
        geom_type=validated.geom_type,
        has_holes=validated.has_holes,
        holes_count=validated.holes_count,
        is_valid=validated.is_valid,
        topology_checked=validated.topology_checked,
        source_path=str(path),
        sha256=_sha256(path),
        license=str(license_info) if license_info else None,
        attribution=str(attribution) if attribution else None,
        osm_relation_id=osm_relation_id,
    )


def resolve_region(region_config: dict[str, Any], workspace: Path,
                   allow_network: bool = False) -> dict[str, Any]:
    """Resolve a region configuration into an explicit validated boundary dictionary."""
    region_id = region_config.get("id", "unnamed-region")
    region_name = region_config.get("name", "")
    source = region_config.get("source")
    common: dict[str, Any] = {"id": region_id, "name": region_name}

    if source == "bbox":
        bbox = region_config.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise BoundaryError("region.bbox deve conter [oeste, sul, leste, norte]")
        west, south, east, north = (float(x) for x in bbox)
        if not (-180.0 <= west < east <= 180.0 and -90.0 <= south < north <= 90.0):
            raise BoundaryError(f"bbox fora dos limites geográficos: {bbox}")
        return {
            **common,
            "boundary_source": "bbox",
            "bbox": [west, south, east, north],
            "crs": "EPSG:4326",
            "has_holes": False,
            "holes_count": 0,
        }

    if source == "boundary_geojson":
        raw_path = region_config.get("boundary_geojson")
        if not raw_path or not isinstance(raw_path, str):
            raise BoundaryError("region.boundary_geojson deve ser um caminho de arquivo não vazio")
        path = Path(raw_path)
        if not path.is_absolute():
            path = (workspace / path).resolve()
        validated = load_boundary_geojson(path)
        rel_id = region_config.get("osm_relation_id") or validated.osm_relation_id
        return {
            **common,
            "boundary_source": "boundary_geojson",
            "path": str(path),
            "sha256": validated.sha256,
            "bbox": list(validated.bbox),
            "geometry_geojson": validated.geometry,
            "crs": validated.crs,
            "geom_type": validated.geom_type,
            "has_holes": validated.has_holes,
            "holes_count": validated.holes_count,
            "is_valid": validated.is_valid,
            "topology_checked": validated.topology_checked,
            "license": validated.license,
            "attribution": validated.attribution,
            "osm_relation_id": rel_id,
        }

    if source == "relation":
        relation_id = region_config.get("osm_relation_id")
        if not relation_id:
            raise BoundaryError("region.source='relation' exige osm_relation_id inteiro")
        if not allow_network:
            return {
                **common,
                "boundary_source": "osm_relation_reference_only",
                "osm_relation_id": relation_id,
                "resolution": "unresolved; remote resolution requires explicit opt-in (--allow-network)",
            }
        # Explicit network opt-in given, but relation fetch requires configured resolver
        resolver_url = region_config.get("boundary_url")
        expected_sha256 = region_config.get("boundary_sha256")
        if not resolver_url or not expected_sha256:
            raise BoundaryError(
                f"Resolução da relação {relation_id} exige region.boundary_url e region.boundary_sha256 pinados; "
                "fetch automático arbitrário sem verificação de hash não é permitido."
            )
        raise BoundaryError("Resolução remota de relação ainda não suportada sem arquivo baixado prévio")

    raise BoundaryError(f"region.source desconhecido: {source!r}")
