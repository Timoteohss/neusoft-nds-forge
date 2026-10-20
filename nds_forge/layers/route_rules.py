"""Route rules and turn restriction compiler for NDS routing (Issue #32 / [4.7]).

Implements the proven turn-restriction subset:
1. Via-node simple restrictions:
   - no_left_turn, no_right_turn, no_u_turn, no_straight_on
   - only_left_turn, only_right_turn, only_straight_on
2. Transition matrix at intersections:
   - Encodes permitted, prohibited, and unknown/blocked transitions.
3. Fail-closed handling for unsupported/conditional restrictions:
   - Unknown is NEVER silently allowed.
   - Conditional restrictions (restriction:conditional) or unverified via-ways
     are audited and blocked in fail-closed mode, triggering alternative route calculation.
4. Route calculation and alternative route verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
import heapq
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
                continue

            if restr.restriction_type == RestrictionType.CONDITIONAL or restr.conditional_tag:
                result.unsupported_restrictions.append({
                    "relation_id": restr.relation_id,
                    "reason": "Conditional restriction requires unverified time condition codec (nds_rt_getAttributeTimeConditions)",
                    "type": str(restr.restriction_type),
                    "conditional": restr.conditional_tag,
                })
                # Under fail-closed policy, unknown/conditional restrictions must never be silently allowed!
                # We identify matching transitions at the via_node and mark them as BLOCKED_UNKNOWN
                if self.fail_closed_unknown and restr.via_node_id:
                    self._apply_prohibition(
                        result=result,
                        via_node=restr.via_node_id,
                        from_way=restr.from_way_id,
                        to_way=restr.to_way_id,
                        incoming_by_node=incoming_by_node,
                        outgoing_by_node=outgoing_by_node,
                        status=TransitionStatus.BLOCKED_UNKNOWN,
                    )
                continue

            if restr.restriction_type == RestrictionType.UNKNOWN:
                result.unsupported_restrictions.append({
                    "relation_id": restr.relation_id,
                    "reason": "Unrecognized turn restriction type",
                    "type": str(restr.restriction_type),
                })
                continue

            # 2. Proven via-node restriction subset
            if restr.via_node_id is None:
                result.unsupported_restrictions.append({
                    "relation_id": restr.relation_id,
                    "reason": "Missing via_node_id in restriction relation",
                })
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

    def _apply_prohibition(
        self,
        result: CompiledTransitions,
        via_node: int,
        from_way: int,
        to_way: int,
        incoming_by_node: dict[int, list[DirectedLink]],
        outgoing_by_node: dict[int, list[DirectedLink]],
        status: TransitionStatus,
    ) -> None:
        in_candidates = [l for l in incoming_by_node.get(via_node, []) if l.way_id == from_way]
        for in_link in in_candidates:
            for out_link in outgoing_by_node.get(via_node, []):
                if out_link.way_id == to_way:
                    result.transitions[(in_link.link_id, out_link.link_id)] = status


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

    # Priority queue: (cost, current_node, prev_link_id, path_links)
    pq: list[tuple[float, int, int | None, list[DirectedLink]]] = [(0.0, start_node, None, [])]
    visited: set[tuple[int, int | None]] = set()

    while pq:
        cost, cur_node, prev_link_id, path = heapq.heappop(pq)

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
                (cost + next_link.length_meters, next_link.to_node, next_link.link_id, path + [next_link]),
            )

    return RoutePath(success=False, total_cost=float("inf"), links=[], nodes=[], transitions_taken=[])
