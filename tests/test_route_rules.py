"""Unit tests for turn-restriction compilation and alternative route routing (Issue #32 / [4.7])."""

from __future__ import annotations

import unittest

from nds_forge.layers.route_rules import (
    CompiledTransitions,
    DirectedLink,
    RestrictionType,
    RouteRulesCompiler,
    TransitionStatus,
    TurnRestriction,
    calculate_route,
)


class RouteRulesTests(unittest.TestCase):
    """Test suite for turn-restriction rules compiler and alternative route pathfinding."""

    def setUp(self) -> None:
        r"""Grid network layout for testing:

            (1) --- L1 (way 10) ---> (2) --- L2 (way 20) ---> (3)
             |                        |                        |
             L4 (way 40)              L3 (way 30)              L5 (way 50)
             |                        |                        |
             v                        v                        v
            (4) --- L6 (way 60) ---> (5) --- L7 (way 70) ---> (6)

        Node 2 is the central junction:
        - In: L1 (from node 1)
        - Out: L2 (to node 3), L3 (to node 5)

        An alternative path from 1 to 5 without using L3 at node 2:
        (1) -> L4 -> (4) -> L6 -> (5)
        """
        self.l1 = DirectedLink(link_id=1, way_id=10, from_node=1, to_node=2, length_meters=100.0)
        self.l2 = DirectedLink(link_id=2, way_id=20, from_node=2, to_node=3, length_meters=100.0)
        self.l3 = DirectedLink(link_id=3, way_id=30, from_node=2, to_node=5, length_meters=100.0)
        self.l4 = DirectedLink(link_id=4, way_id=40, from_node=1, to_node=4, length_meters=120.0)
        self.l5 = DirectedLink(link_id=5, way_id=50, from_node=3, to_node=6, length_meters=100.0)
        self.l6 = DirectedLink(link_id=6, way_id=60, from_node=4, to_node=5, length_meters=120.0)
        self.l7 = DirectedLink(link_id=7, way_id=70, from_node=5, to_node=6, length_meters=100.0)

        self.links = [self.l1, self.l2, self.l3, self.l4, self.l5, self.l6, self.l7]

    def test_legal_route_without_restrictions(self) -> None:
        compiler = RouteRulesCompiler()
        rules = compiler.compile(self.links, restrictions=[])

        # Routing from 1 to 5: direct path via L1 -> L2 or L1 -> L3
        # L1 (100) + L3 (100) = 200m
        # L4 (120) + L6 (120) = 240m
        route = calculate_route(self.links, rules, start_node=1, target_node=5)
        self.assertTrue(route.success)
        self.assertEqual(route.total_cost, 200.0)
        self.assertEqual([l.link_id for l in route.links], [1, 3])
        self.assertEqual(route.nodes, [1, 2, 5])
        self.assertEqual(route.transitions_taken, [(1, 3)])

    def test_prohibited_turn_restriction_finds_alternative_route(self) -> None:
        # Restriction: at node 2, turning from way 10 (L1) to way 30 (L3) is PROHIBITED
        restr = TurnRestriction(
            relation_id=5001,
            restriction_type=RestrictionType.NO_RIGHT_TURN,
            from_way_id=10,
            via_node_id=2,
            to_way_id=30,
        )

        compiler = RouteRulesCompiler()
        rules = compiler.compile(self.links, restrictions=[restr])

        self.assertEqual(rules.compiled_restrictions_count, 1)
        self.assertEqual(rules.get_status(1, 3), TransitionStatus.PROHIBITED_LEGAL)
        self.assertFalse(rules.is_allowed(1, 3))

        # Routing from 1 to 5 must avoid (1 -> 3) and take alternative path (1 -> 4 -> 6)
        route = calculate_route(self.links, rules, start_node=1, target_node=5)
        self.assertTrue(route.success)
        self.assertEqual(route.total_cost, 240.0)
        self.assertEqual([l.link_id for l in route.links], [4, 6])
        self.assertEqual(route.nodes, [1, 4, 5])
        self.assertNotIn((1, 3), route.transitions_taken)

    def test_mandatory_only_turn_restriction(self) -> None:
        # Restriction: at node 2, from way 10, ONLY straight on to way 20 is permitted
        restr = TurnRestriction(
            relation_id=5002,
            restriction_type=RestrictionType.ONLY_STRAIGHT_ON,
            from_way_id=10,
            via_node_id=2,
            to_way_id=20,
        )

        compiler = RouteRulesCompiler()
        rules = compiler.compile(self.links, restrictions=[restr])

        self.assertEqual(rules.compiled_restrictions_count, 1)
        # Transition (1, 2) is allowed (it matches the only_straight_on target)
        self.assertEqual(rules.get_status(1, 2), TransitionStatus.ALLOWED)
        # All other outgoing transitions from 1 at node 2 (e.g. 1 -> 3) are prohibited
        self.assertEqual(rules.get_status(1, 3), TransitionStatus.PROHIBITED_LEGAL)

    def test_unknown_conditional_restriction_is_never_silently_allowed(self) -> None:
        # Conditional restriction: restriction:conditional = no_right_turn @ (07:00-09:00)
        restr = TurnRestriction(
            relation_id=5003,
            restriction_type=RestrictionType.CONDITIONAL,
            from_way_id=10,
            via_node_id=2,
            to_way_id=30,
            conditional_tag="no_right_turn @ (07:00-09:00)",
        )

        compiler = RouteRulesCompiler(fail_closed_unknown=True)
        rules = compiler.compile(self.links, restrictions=[restr])

        # Must be audited in unsupported_restrictions
        self.assertEqual(len(rules.unsupported_restrictions), 1)
        self.assertIn("Conditional restriction requires unverified time condition codec", rules.unsupported_restrictions[0]["reason"])

        # Under fail-closed policy: unknown is NEVER silently allowed!
        self.assertEqual(rules.get_status(1, 3), TransitionStatus.BLOCKED_UNKNOWN)
        self.assertFalse(rules.is_allowed(1, 3))

        # Pathfinder MUST NOT take the blocked unknown transition; it must take the alternative route
        route = calculate_route(self.links, rules, start_node=1, target_node=5)
        self.assertTrue(route.success)
        self.assertEqual([l.link_id for l in route.links], [4, 6])
        self.assertEqual(route.total_cost, 240.0)

    def test_unsupported_via_way_audited(self) -> None:
        # Multi-link via_way restriction
        restr = TurnRestriction(
            relation_id=5004,
            restriction_type=RestrictionType.NO_LEFT_TURN,
            from_way_id=10,
            via_way_id=999,
            to_way_id=30,
        )

        compiler = RouteRulesCompiler()
        rules = compiler.compile(self.links, restrictions=[restr])

        self.assertEqual(len(rules.unsupported_restrictions), 1)
        self.assertIn("Via-way multi-link restriction requires continued turn codec", rules.unsupported_restrictions[0]["reason"])


if __name__ == "__main__":
    unittest.main()
