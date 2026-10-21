"""Canonical loss-aware OSM intermediate data model.

Provides versioned schemas and dataclasses for OSM Nodes, Ways, Relations,
ordered members, raw tags, clipping boundary lineage, and explicit
partial/missing reference tracking.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schema_validation import validate_json_schema

SCHEMA_VERSION: int = 1


class OSMModelError(ValueError):
    """Raised when an OSM model record or dataset violates the contract."""


@dataclass(frozen=True)
class OSMProvenance:
    """Provenance and legal attribution for an OSM dataset."""
    source: str
    license: str = "ODbL-1.0"
    attribution: str = "© OpenStreetMap contributors"
    path: str | None = None
    sha256: str | None = None
    timestamp: str | None = None

    def __post_init__(self) -> None:
        if not self.source:
            raise OSMModelError("OSMProvenance source cannot be empty")
        if not self.license:
            raise OSMModelError("OSMProvenance license cannot be empty")
        if not self.attribution:
            raise OSMModelError("OSMProvenance attribution cannot be empty")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source": self.source,
            "license": self.license,
            "attribution": self.attribution,
        }
        if self.path is not None:
            result["path"] = self.path
        if self.sha256 is not None:
            result["sha256"] = self.sha256
        if self.timestamp is not None:
            result["timestamp"] = self.timestamp
        return dict(sorted(result.items()))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OSMProvenance:
        if not isinstance(data, dict):
            raise OSMModelError("provenance must be an object")
        return cls(
            source=data.get("source", ""),
            license=data.get("license", "ODbL-1.0"),
            attribution=data.get("attribution", "© OpenStreetMap contributors"),
            path=data.get("path"),
            sha256=data.get("sha256"),
            timestamp=data.get("timestamp"),
        )


@dataclass(frozen=True)
class ClipLineage:
    """Lineage information regarding boundary clipping and extraction policy."""
    region_id: str | None = None
    boundary_sha256: str | None = None
    policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.region_id is not None:
            result["region_id"] = self.region_id
        if self.boundary_sha256 is not None:
            result["boundary_sha256"] = self.boundary_sha256
        if self.policy:
            result["policy"] = dict(sorted(self.policy.items()))
        return dict(sorted(result.items()))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClipLineage:
        if not isinstance(data, dict):
            raise OSMModelError("clip_lineage must be an object")
        return cls(
            region_id=data.get("region_id"),
            boundary_sha256=data.get("boundary_sha256"),
            policy=data.get("policy", {}),
        )


@dataclass
class OSMNode:
    """An individual OSM node entity with raw tags and coordinates."""
    id: int
    lat: float
    lon: float
    tags: dict[str, str] = field(default_factory=dict)
    is_boundary_cut: bool = False
    is_synthetic: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, int):
            raise OSMModelError(f"Node id must be integer, got {type(self.id)}")
        if not isinstance(self.lat, (int, float)) or not (-90.0 <= self.lat <= 90.0):
            raise OSMModelError(f"Node {self.id} latitude out of bounds [-90, 90]: {self.lat}")
        if not isinstance(self.lon, (int, float)) or not (-180.0 <= self.lon <= 180.0):
            raise OSMModelError(f"Node {self.id} longitude out of bounds [-180, 180]: {self.lon}")
        self.lat = float(self.lat)
        self.lon = float(self.lon)
        self.tags = {str(k): str(v) for k, v in sorted(self.tags.items())}

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "lat": self.lat,
            "lon": self.lon,
        }
        if self.tags:
            result["tags"] = dict(sorted(self.tags.items()))
        if self.is_boundary_cut:
            result["is_boundary_cut"] = True
        if self.is_synthetic:
            result["is_synthetic"] = True
        return dict(sorted(result.items()))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OSMNode:
        if not isinstance(data, dict):
            raise OSMModelError("node record must be an object")
        for req in ("id", "lat", "lon"):
            if req not in data:
                raise OSMModelError(f"node record missing required field: {req}")
        return cls(
            id=data["id"],
            lat=data["lat"],
            lon=data["lon"],
            tags=data.get("tags", {}),
            is_boundary_cut=data.get("is_boundary_cut", False),
            is_synthetic=data.get("is_synthetic", False),
        )


@dataclass
class OSMWay:
    """An ordered sequence of node IDs representing an OSM way."""
    id: int
    nodes: list[int] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    coordinates: list[tuple[float, float]] | None = None
    missing_nodes: list[int] = field(default_factory=list)
    is_boundary_cut: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, int):
            raise OSMModelError(f"Way id must be integer, got {type(self.id)}")
        if not isinstance(self.nodes, list) or not all(isinstance(n, int) for n in self.nodes):
            raise OSMModelError(f"Way {self.id} nodes must be a list of integer IDs")
        if self.coordinates is not None:
            if not isinstance(self.coordinates, list):
                raise OSMModelError(f"Way {self.id} coordinates must be a list of (lon, lat) tuples")
            coords: list[tuple[float, float]] = []
            for coord in self.coordinates:
                if (not isinstance(coord, (list, tuple)) or len(coord) != 2 or
                        not all(isinstance(c, (int, float)) for c in coord)):
                    raise OSMModelError(f"Way {self.id} invalid coordinate: {coord}")
                lon, lat = float(coord[0]), float(coord[1])
                if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
                    raise OSMModelError(f"Way {self.id} coordinate out of bounds: ({lon}, {lat})")
                coords.append((lon, lat))
            self.coordinates = coords
        self.tags = {str(k): str(v) for k, v in sorted(self.tags.items())}

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "nodes": list(self.nodes),
        }
        if self.tags:
            result["tags"] = dict(sorted(self.tags.items()))
        if self.coordinates is not None:
            result["coordinates"] = [[lon, lat] for lon, lat in self.coordinates]
        if self.missing_nodes:
            result["missing_nodes"] = sorted(self.missing_nodes)
        if self.is_boundary_cut:
            result["is_boundary_cut"] = True
        return dict(sorted(result.items()))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OSMWay:
        if not isinstance(data, dict):
            raise OSMModelError("way record must be an object")
        for req in ("id", "nodes"):
            if req not in data:
                raise OSMModelError(f"way record missing required field: {req}")
        coords = None
        if "coordinates" in data and data["coordinates"] is not None:
            coords = [(float(c[0]), float(c[1])) for c in data["coordinates"]]
        return cls(
            id=data["id"],
            nodes=data["nodes"],
            tags=data.get("tags", {}),
            coordinates=coords,
            missing_nodes=data.get("missing_nodes", []),
            is_boundary_cut=data.get("is_boundary_cut", False),
        )


@dataclass(frozen=True)
class RelationMember:
    """A typed reference member of an OSM relation."""
    type: str  # "node", "way", or "relation"
    ref: int
    role: str = ""

    def __post_init__(self) -> None:
        if self.type not in {"node", "way", "relation"}:
            raise OSMModelError(f"RelationMember type must be 'node', 'way', or 'relation', got {self.type}")
        if not isinstance(self.ref, int):
            raise OSMModelError(f"RelationMember ref must be integer, got {type(self.ref)}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "role": str(self.role),
            "type": self.type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RelationMember:
        if not isinstance(data, dict):
            raise OSMModelError("relation member must be an object")
        for req in ("type", "ref", "role"):
            if req not in data:
                raise OSMModelError(f"relation member missing required field: {req}")
        return cls(
            type=str(data["type"]),
            ref=int(data["ref"]),
            role=str(data["role"]),
        )


@dataclass
class OSMRelation:
    """An OSM relation grouping nodes, ways, and sub-relations with roles."""
    id: int
    members: list[RelationMember] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    missing_members: list[RelationMember] = field(default_factory=list)
    is_partial: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, int):
            raise OSMModelError(f"Relation id must be integer, got {type(self.id)}")
        if not isinstance(self.members, list):
            raise OSMModelError(f"Relation {self.id} members must be a list")
        self.tags = {str(k): str(v) for k, v in sorted(self.tags.items())}

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "members": [m.to_dict() for m in self.members],
        }
        if self.tags:
            result["tags"] = dict(sorted(self.tags.items()))
        if self.missing_members:
            result["missing_members"] = [m.to_dict() for m in self.missing_members]
        if self.is_partial:
            result["is_partial"] = True
        return dict(sorted(result.items()))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OSMRelation:
        if not isinstance(data, dict):
            raise OSMModelError("relation record must be an object")
        for req in ("id", "members"):
            if req not in data:
                raise OSMModelError(f"relation record missing required field: {req}")
        members = [RelationMember.from_dict(m) for m in data["members"]]
        missing_members = [RelationMember.from_dict(m) for m in data.get("missing_members", [])]
        return cls(
            id=data["id"],
            members=members,
            tags=data.get("tags", {}),
            missing_members=missing_members,
            is_partial=data.get("is_partial", False),
        )


@dataclass(frozen=True)
class ReferenceAuditReport:
    """Audit report detailing unresolved cross-references in the dataset."""
    missing_node_refs: dict[int, list[int]]
    missing_member_refs: dict[int, list[RelationMember]]
    total_nodes: int
    total_ways: int
    total_relations: int
    complete_ways_count: int
    partial_ways_count: int
    complete_relations_count: int
    partial_relations_count: int

    @property
    def has_missing_references(self) -> bool:
        return bool(self.missing_node_refs or self.missing_member_refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete_relations_count": self.complete_relations_count,
            "complete_ways_count": self.complete_ways_count,
            "has_missing_references": self.has_missing_references,
            "missing_member_refs": {
                str(rel_id): [m.to_dict() for m in members]
                for rel_id, members in sorted(self.missing_member_refs.items())
            },
            "missing_node_refs": {
                str(way_id): sorted(nodes)
                for way_id, nodes in sorted(self.missing_node_refs.items())
            },
            "partial_relations_count": self.partial_relations_count,
            "partial_ways_count": self.partial_ways_count,
            "total_nodes": self.total_nodes,
            "total_relations": self.total_relations,
            "total_ways": self.total_ways,
        }


@dataclass
class OSMDataset:
    """A canonical intermediate OSM dataset containing nodes, ways, and relations."""
    provenance: OSMProvenance
    schema_version: int = SCHEMA_VERSION
    clip_lineage: ClipLineage | None = None
    nodes: dict[int, OSMNode] = field(default_factory=dict)
    ways: dict[int, OSMWay] = field(default_factory=dict)
    relations: dict[int, OSMRelation] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise OSMModelError(
                f"Unsupported schema_version: {self.schema_version} (expected {SCHEMA_VERSION})"
            )

    def add_node(self, node: OSMNode) -> None:
        self.nodes[node.id] = node

    def add_way(self, way: OSMWay) -> None:
        self.ways[way.id] = way

    def add_relation(self, relation: OSMRelation) -> None:
        self.relations[relation.id] = relation

    def audit_references(self, update_entities: bool = True) -> ReferenceAuditReport:
        """Verify cross-entity references and explicitly mark partials and missing refs."""
        missing_nodes: dict[int, list[int]] = {}
        for way in self.ways.values():
            way_missing = [node_id for node_id in way.nodes if node_id not in self.nodes]
            if way_missing:
                missing_nodes[way.id] = way_missing
                if update_entities:
                    way.missing_nodes = way_missing

        missing_members: dict[int, list[RelationMember]] = {}
        for rel in self.relations.values():
            rel_missing: list[RelationMember] = []
            for m in rel.members:
                exists = False
                if m.type == "node" and m.ref in self.nodes or m.type == "way" and m.ref in self.ways or m.type == "relation" and m.ref in self.relations:
                    exists = True
                if not exists:
                    rel_missing.append(m)
            if rel_missing:
                missing_members[rel.id] = rel_missing
                if update_entities:
                    rel.missing_members = rel_missing
                    rel.is_partial = True

        partial_ways = len(missing_nodes)
        partial_rels = len(missing_members)
        return ReferenceAuditReport(
            missing_node_refs=missing_nodes,
            missing_member_refs=missing_members,
            total_nodes=len(self.nodes),
            total_ways=len(self.ways),
            total_relations=len(self.relations),
            complete_ways_count=len(self.ways) - partial_ways,
            partial_ways_count=partial_ways,
            complete_relations_count=len(self.relations) - partial_rels,
            partial_relations_count=partial_rels,
        )

    def to_dict(self) -> dict[str, Any]:
        """Produce a deterministic dictionary representation."""
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "provenance": self.provenance.to_dict(),
            "nodes": [self.nodes[k].to_dict() for k in sorted(self.nodes)],
            "ways": [self.ways[k].to_dict() for k in sorted(self.ways)],
            "relations": [self.relations[k].to_dict() for k in sorted(self.relations)],
        }
        if self.clip_lineage is not None:
            result["clip_lineage"] = self.clip_lineage.to_dict()
        return dict(sorted(result.items()))

    def to_json(self, indent: int | None = 2) -> str:
        """Produce deterministic, sort-keyed JSON string."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OSMDataset:
        if not isinstance(data, dict):
            raise OSMModelError("OSMDataset data must be an object")
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise OSMModelError(f"Unsupported schema_version: {version} (expected {SCHEMA_VERSION})")
        if "provenance" not in data:
            raise OSMModelError("OSMDataset missing required field: provenance")

        provenance = OSMProvenance.from_dict(data["provenance"])
        clip_lineage = None
        if "clip_lineage" in data and data["clip_lineage"] is not None:
            clip_lineage = ClipLineage.from_dict(data["clip_lineage"])

        nodes = {
            item["id"]: OSMNode.from_dict(item)
            for item in data.get("nodes", [])
        }
        ways = {
            item["id"]: OSMWay.from_dict(item)
            for item in data.get("ways", [])
        }
        relations = {
            item["id"]: OSMRelation.from_dict(item)
            for item in data.get("relations", [])
        }

        return cls(
            schema_version=version,
            provenance=provenance,
            clip_lineage=clip_lineage,
            nodes=nodes,
            ways=ways,
            relations=relations,
        )

    @classmethod
    def from_json(cls, json_str: str) -> OSMDataset:
        try:
            data = json.loads(json_str)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OSMModelError(f"Failed to decode JSON: {exc}") from exc
        return cls.from_dict(data)

    def validate_against_schema(self, schema_path: Path | None = None) -> bool:
        """Validate data; return False if the schema or optional validator is unavailable."""
        if schema_path is None:
            default_path = Path(__file__).resolve().parents[1] / "schemas" / "osm-model.schema.json"
            if default_path.is_file():
                schema_path = default_path
        if schema_path is None:
            return False
        try:
            return validate_json_schema(self.to_dict(), schema_path)
        except Exception as exc:
            raise OSMModelError(f"OSM schema validation failed: {exc}") from exc
