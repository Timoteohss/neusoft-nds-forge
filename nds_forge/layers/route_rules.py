"""Route rules and turn restriction compiler for NDS routing (Issue #32 / [4.7]).

Implements the proven turn-restriction subset:
1. Via-node simple restrictions:
   - no_left_turn, no_right_turn, no_u_turn, no_straight_on
   - only_left_turn, only_right_turn, only_straight_on
2. Transition matrix at intersections:
   - Encodes permitted, prohibited, and unknown/blocked transitions.
3. Fail-closed handling for unsupported/conditional restrictions:
   - Unknown and conditional via-node restrictions block every affected exit.
   - Via-way/missing-via restrictions reject compilation in fail-closed mode.
4. In-memory route calculation for tests. This module does not encode these
   rules into Neusoft ROUTE.NDS and is not a native-routing validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
import heapq
from itertools import count
from typing import Any


class RestrictionType(str, Enum):
    """Known turn restriction types."""
    NO_LEFT_TURN = "no_left_turn"
    NO_RIGHT_TURN = "no_right_turn"
    NO_U_TURN = "no_u_turn"
    NO_STRAIGHT_ON = "no_straight_on"
    ONLY_LEFT_TURN = "only_left_turn"
    ONLY_RIGHT_TURN = "only_right_turn"
    ONLY_STRAIGHT_ON = "only_straight_on"
    CONDITIONAL = "restriction:conditional"
    UNKNOWN = "unknown"

    @classmethod
    def from_tag(cls, tag_value: str) -> "RestrictionType":
        val = tag_value.strip().lower()
        for member in cls:
            if member.value == val:
                return member
        if "conditional" in val or "@" in val:
            return cls.CONDITIONAL
        return cls.UNKNOWN


class TransitionStatus(IntEnum):
    """Status of an intersection transition from an incoming link to an outgoing link."""
    ALLOWED = 0
    PROHIBITED_LEGAL = 1
    BLOCKED_UNKNOWN = 2


@dataclass(frozen=True)
class TurnRestriction:
    """Represents an OSM turn-restriction relation."""
    relation_id: int
    restriction_type: RestrictionType
    from_way_id: int
    to_way_id: int
    via_node_id: int | None = None
    via_way_id: int | None = None
    conditional_tag: str | None = None


@dataclass(frozen=True)
class DirectedLink:
    """Directed road segment between two intersection nodes."""
    link_id: int
    way_id: int
    from_node: int
    to_node: int
    length_meters: float = 100.0


@dataclass
class CompiledTransitions:
    """Result of compiling turn restrictions onto a set of directed links."""
    transitions: dict[tuple[int, int], TransitionStatus] = field(default_factory=dict)
    unsupported_restrictions: list[dict[str, Any]] = field(default_factory=list)
    compiled_restrictions_count: int = 0

    def is_allowed(self, from_link: int, to_link: int) -> bool:
        """Check if transition is strictly permitted."""
        return self.transitions.get((from_link, to_link), TransitionStatus.ALLOWED) == TransitionStatus.ALLOWED

    def get_status(self, from_link: int, to_link: int) -> TransitionStatus:
        """Get the TransitionStatus for a link-to-link transition."""
        return self.transitions.get((from_link, to_link), TransitionStatus.ALLOWED)


class UnsupportedRestrictionError(ValueError):
    """A fail-closed restriction cannot be represented by this local compiler."""


class RouteRulesCompiler:
    """Compiles turn restrictions into intersection transition matrices."""

    def __init__(self, fail_closed_unknown: bool = True) -> None:
        self.fail_closed_unknown = fail_closed_unknown

    def compile(
        self,
        links: list[DirectedLink],
        restrictions: list[TurnRestriction],
    ) -> CompiledTransitions:
        """Compile restrictions into link-to-link transition rules."""
        result = CompiledTransitions()

        # Build adjacency: node -> incoming links and outgoing links
        incoming_by_node: dict[int, list[DirectedLink]] = {}
        outgoing_by_node: dict[int, list[DirectedLink]] = {}
        for link in links:
            incoming_by_node.setdefault(link.to_node, []).append(link)
            outgoing_by_node.setdefault(link.from_node, []).append(link)

        for restr in restrictions:
            # 1. Check for unsupported multi-link via-way or conditional restrictions
            if restr.via_way_id is not None:
                result.unsupported_restrictions.append({
                    "relation_id": restr.relation_id,
                    "reason": "Via-way multi-link restriction requires continued turn codec (nds_rt_getContinuedTurnRestrictions) not compiled in local tile",
                    "type": str(restr.restriction_type),
                })
                if self.fail_closed_unknown:
                    raise UnsupportedRestrictionError(
                        f"Relation {restr.relation_id}: via-way restrictions are not supported; "
                        "refusing to produce permissive transitions")
                continue

            if restr.restriction_type == RestrictionType.CONDITIONAL or restr.conditional_tag:
                result.unsupported_restrictions.append({
                    "relation_id": restr.relation_id,
                    "reason": "Conditional restriction requires unverified time condition codec (nds_rt_getAttributeTimeConditions)",
                    "type": str(restr.restriction_type),
                    "conditional": restr.conditional_tag,
                })
                if self.fail_closed_unknown:
                    self._block_unknown(result, restr, incoming_by_node, outgoing_by_node)
                continue

            if restr.restriction_type == RestrictionType.UNKNOWN:
                result.unsupported_restrictions.append({
                    "relation_id": restr.relation_id,
                    "reason": "Unrecognized turn restriction type",
                    "type": str(restr.restriction_type),
                })
                if self.fail_closed_unknown:
                    self._block_unknown(result, restr, incoming_by_node, outgoing_by_node)
                continue

            # 2. Proven via-node restriction subset
            if restr.via_node_id is None:
                result.unsupported_restrictions.append({
                    "relation_id": restr.relation_id,
                    "reason": "Missing via_node_id in restriction relation",
                })
                if self.fail_closed_unknown:
                    raise UnsupportedRestrictionError(
                        f"Relation {restr.relation_id}: missing via node; cannot block safely")
                continue

            via = restr.via_node_id
            in_candidates = [l for l in incoming_by_node.get(via, []) if l.way_id == restr.from_way_id]
            out_candidates = outgoing_by_node.get(via, [])

            if not in_candidates:
                continue

            result.compiled_restrictions_count += 1

            # Prohibitory restrictions (no_*)
            if restr.restriction_type in (
                RestrictionType.NO_LEFT_TURN,
                RestrictionType.NO_RIGHT_TURN,
                RestrictionType.NO_STRAIGHT_ON,
                RestrictionType.NO_U_TURN,
            ):
                for in_link in in_candidates:
                    for out_link in out_candidates:
                        if out_link.way_id == restr.to_way_id:
                            result.transitions[(in_link.link_id, out_link.link_id)] = TransitionStatus.PROHIBITED_LEGAL

            # Mandatory restrictions (only_*)
            elif restr.restriction_type in (
                RestrictionType.ONLY_LEFT_TURN,
                RestrictionType.ONLY_RIGHT_TURN,
                RestrictionType.ONLY_STRAIGHT_ON,
            ):
                for in_link in in_candidates:
                    for out_link in out_candidates:
                        if out_link.way_id != restr.to_way_id:
                            # Prohibit all outgoing choices EXCEPT the mandatory target way
                            result.transitions[(in_link.link_id, out_link.link_id)] = TransitionStatus.PROHIBITED_LEGAL

        return result

    def _block_unknown(
        self,
        result: CompiledTransitions,
        restriction: TurnRestriction,
        incoming_by_node: dict[int, list[DirectedLink]],
        outgoing_by_node: dict[int, list[DirectedLink]],
    ) -> None:
        """Block every exit of the affected incoming way, including only_* cases.

        Without a verified condition/type we cannot safely infer whether it is
        the named target or every other exit that must be prohibited. If the
        via node is absent, refuse compilation rather than silently allow it.
        """
        if restriction.via_node_id is None:
            raise UnsupportedRestrictionError(
                f"Relation {restriction.relation_id}: unknown restriction has no via node")
        incoming = incoming_by_node.get(restriction.via_node_id, [])
        for in_link in incoming:
            if in_link.way_id != restriction.from_way_id:
                continue
            for out_link in outgoing_by_node.get(restriction.via_node_id, []):
                result.transitions[(in_link.link_id, out_link.link_id)] = TransitionStatus.BLOCKED_UNKNOWN


@dataclass
class RoutePath:
    """Calculated path result."""
    success: bool
    total_cost: float
    links: list[DirectedLink]
    nodes: list[int]
    transitions_taken: list[tuple[int, int]]


def calculate_route(
    links: list[DirectedLink],
    compiled_rules: CompiledTransitions,
    start_node: int,
    target_node: int,
) -> RoutePath:
    """Calculate the shortest route obeying compiled turn restrictions.

    State in Dijkstra: (cost, current_node, previous_link_id).
    Transitions that are PROHIBITED_LEGAL or BLOCKED_UNKNOWN are rejected.
    """
    links_by_from: dict[int, list[DirectedLink]] = {}
    link_by_id: dict[int, DirectedLink] = {}
    for l in links:
        links_by_from.setdefault(l.from_node, []).append(l)
        link_by_id[l.link_id] = l

    # Stable queue serials avoid comparing DirectedLink objects when equal-cost
    # paths merge onto the same link.
    serial = count()
    pq: list[tuple[float, int, int, int | None, list[DirectedLink]]] = [
        (0.0, next(serial), start_node, None, [])]
    visited: set[tuple[int, int | None]] = set()

    while pq:
        cost, _, cur_node, prev_link_id, path = heapq.heappop(pq)

        if cur_node == target_node:
            nodes = [start_node] + [l.to_node for l in path]
            transitions = []
            for i in range(len(path) - 1):
                transitions.append((path[i].link_id, path[i + 1].link_id))
            return RoutePath(
                success=True,
                total_cost=cost,
                links=path,
                nodes=nodes,
                transitions_taken=transitions,
            )

        state_key = (cur_node, prev_link_id)
        if state_key in visited:
            continue
        visited.add(state_key)

        for next_link in links_by_from.get(cur_node, []):
            if prev_link_id is not None:
                # Check transition rules
                status = compiled_rules.get_status(prev_link_id, next_link.link_id)
                if status != TransitionStatus.ALLOWED:
                    # Pruned: prohibited or blocked unknown
                    continue

            heapq.heappush(
                pq,
                (cost + next_link.length_meters, next(serial), next_link.to_node,
                 next_link.link_id, path + [next_link]),
            )

    return RoutePath(success=False, total_cost=float("inf"), links=[], nodes=[], transitions_taken=[])
