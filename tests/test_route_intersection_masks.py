"""Tests for mapping compiled prohibitions onto native attr-8 mask words."""
from __future__ import annotations

import unittest

from nds_forge.layers.route_graph import _intersection_masks
from nds_forge.layers.route_rules import CompiledTransitions, TransitionStatus


def _graph(edges):
    return {"links": edges}


class IntersectionMaskTests(unittest.TestCase):
    def test_forbidden_transition_clears_one_bit(self) -> None:
        graph = _graph([
            {"a": 0, "b": 1, "way_id": 10},
            {"a": 0, "b": 2, "way_id": 20},
            {"a": 0, "b": 3, "way_id": 30},
        ])
        keys = [0, 1, 2, 3]
        node_links = {
            0: [(False, None, 0, 1), (False, None, 1, 1), (False, None, 2, 1)],
            1: [(False, None, 0, 0)],
            2: [(False, None, 1, 0)],
            3: [(False, None, 2, 0)],
        }
        compiled = CompiledTransitions(
            transitions={(3, 4): TransitionStatus.PROHIBITED_LEGAL})  # edge 1 dir 1 -> edge 2
        masks, audits = _intersection_masks(graph, keys, node_links, compiled, 1)
        # default 0b111_111_111, clear bit entry 1 * 3 + target 2 = bit 5
        self.assertEqual(masks, [(0, 0x1DF)])
        self.assertEqual(audits, [])

    def test_no_restriction_emits_no_word(self) -> None:
        graph = _graph([{"a": 0, "b": 1, "way_id": 10}])
        node_links = {0: [(False, None, 0, 1)], 1: [(False, None, 0, 0)]}
        compiled = CompiledTransitions(transitions={})
        masks, audits = _intersection_masks(graph, [0, 1], node_links, compiled, 1)
        self.assertEqual((masks, audits), ([], []))

    def test_degree_above_four_is_audited(self) -> None:
        edges = [{"a": 0, "b": i + 1, "way_id": 10 + i} for i in range(5)]
        node_links = {0: [(False, None, i, 1) for i in range(5)]}
        for i in range(5):
            node_links[i + 1] = [(False, None, i, 0)]
        compiled = CompiledTransitions(
            transitions={(1, 2): TransitionStatus.PROHIBITED_LEGAL})
        masks, audits = _intersection_masks(_graph(edges), list(range(6)), node_links,
                                            compiled, 7)
        self.assertEqual(masks, [])
        self.assertEqual(len(audits), 1)
        self.assertIn("exceeds the 16-bit mask", audits[0]["reason"])


if __name__ == "__main__":
    unittest.main()

class TiledRestrictionTests(unittest.TestCase):
    def test_tagged_node_keys_still_compile_transitions(self) -> None:
        from nds_forge.layers.route_graph import _compile_tile_transitions
        from nds_forge.layers.route_rules import RestrictionType, TurnRestriction

        a, b, c = ("osm", 1), ("osm", 2), ("osm", 3)
        graph = {
            "nodes": {a: (0.0, 0.0), b: (0.0, 1.0), c: (0.0, 2.0)},
            "links": [
                {"a": a, "b": b, "way_id": 10, "direction": "forward",
                 "attrs": {"length_raw": 100}},
                {"a": b, "b": c, "way_id": 20, "direction": "forward",
                 "attrs": {"length_raw": 100}},
            ],
        }
        restriction = TurnRestriction(
            relation_id=1, restriction_type=RestrictionType.NO_LEFT_TURN,
            from_way_id=10, to_way_id=20, via_node_id=2)
        rows, audits, compiled = _compile_tile_transitions(graph, [restriction], 9)
        self.assertEqual([(r["from_directed_link"], r["to_directed_link"]) for r in rows], [(0, 2)])
        self.assertEqual(rows[0]["status"], "PROHIBITED_LEGAL")
        self.assertEqual(audits, [])

        node_links = {a: [(False, None, 0, 1)], b: [(False, None, 0, 0), (False, None, 1, 1)],
                      c: [(False, None, 1, 0)]}
        masks, _ = _intersection_masks(graph, [a, b, c], node_links, compiled, 9)
        self.assertEqual(masks, [(1, 0xD)])
