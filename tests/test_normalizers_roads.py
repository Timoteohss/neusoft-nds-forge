"""Table-driven unit tests for road attribute normalization.

Validates highway classes, oneway/roundabout rules, access precedence,
speed limit parsing, barrier detection, conditional tag preservation,
and unknown tag retention policies.
"""

from __future__ import annotations

import unittest

from nds_forge.normalizers.roads import (
    AccessLevel,
    HighwayClass,
    OnewayDirection,
    RoadNormalizationError,
    normalize_road_tags,
)


class RoadNormalizerTableTests(unittest.TestCase):
    """Table-driven fixtures for road tag normalization."""

    def test_oneway_table_driven_fixtures(self) -> None:
        table = [
            ({"highway": "primary", "oneway": "yes"}, OnewayDirection.FORWARD),
            ({"highway": "primary", "oneway": "1"}, OnewayDirection.FORWARD),
            ({"highway": "primary", "oneway": "true"}, OnewayDirection.FORWARD),
            ({"highway": "primary", "oneway": "-1"}, OnewayDirection.REVERSE),
            ({"highway": "primary", "oneway": "reverse"}, OnewayDirection.REVERSE),
            ({"highway": "primary", "oneway": "no"}, OnewayDirection.BOTH),
            ({"highway": "primary", "oneway": "0"}, OnewayDirection.BOTH),
            ({"highway": "primary", "oneway": "false"}, OnewayDirection.BOTH),
            ({"highway": "primary"}, OnewayDirection.BOTH),
            ({"highway": "motorway"}, OnewayDirection.FORWARD),  # Motorway defaults to forward
        ]
        for tags, expected_oneway in table:
            with self.subTest(tags=tags):
                road = normalize_road_tags(tags, way_id=101)
                self.assertEqual(road.oneway, expected_oneway)
                self.assertEqual(road.raw_tags, tags)

    def test_roundabout_fixtures(self) -> None:
        table = [
            ({"highway": "primary", "junction": "roundabout"}, True, OnewayDirection.FORWARD),
            ({"highway": "secondary", "junction": "circular"}, True, OnewayDirection.FORWARD),
            ({"highway": "tertiary", "junction": "roundabout", "oneway": "no"}, True, OnewayDirection.BOTH),
            ({"highway": "residential"}, False, OnewayDirection.BOTH),
        ]
        for tags, expected_roundabout, expected_oneway in table:
            with self.subTest(tags=tags):
                road = normalize_road_tags(tags, way_id=102)
                self.assertEqual(road.is_roundabout, expected_roundabout)
                self.assertEqual(road.oneway, expected_oneway)
                self.assertEqual(road.raw_tags, tags)

    def test_access_precedence_and_private_fixtures(self) -> None:
        table = [
            # 1. Plain private road
            (
                {"highway": "residential", "access": "private"},
                AccessLevel.PRIVATE, AccessLevel.PRIVATE, AccessLevel.PRIVATE,
            ),
            # 2. General no, but motorcar allowed
            (
                {"highway": "service", "access": "no", "motorcar": "yes"},
                AccessLevel.NO, AccessLevel.PUBLIC, AccessLevel.NO,
            ),
            # 3. Vehicle private, bicycle permissive
            (
                {"highway": "track", "vehicle": "private", "bicycle": "permissive"},
                AccessLevel.PUBLIC, AccessLevel.PRIVATE, AccessLevel.PERMISSIVE,
            ),
            # 4. Destination only for motor_vehicle
            (
                {"highway": "residential", "motor_vehicle": "destination"},
                AccessLevel.PUBLIC, AccessLevel.DESTINATION, AccessLevel.PUBLIC,
            ),
            # 5. Customers access
            (
                {"highway": "service", "access": "customers"},
                AccessLevel.CUSTOMERS, AccessLevel.CUSTOMERS, AccessLevel.CUSTOMERS,
            ),
        ]
        for tags, exp_gen, exp_motorcar, exp_bicycle in table:
            with self.subTest(tags=tags):
                road = normalize_road_tags(tags, way_id=103)
                self.assertEqual(road.general_access, exp_gen)
                self.assertEqual(road.motorcar_access, exp_motorcar)
                self.assertEqual(road.bicycle_access, exp_bicycle)
                self.assertEqual(road.raw_tags, tags)

    def test_maxspeed_fixtures(self) -> None:
        table = [
            # Plain numeric km/h
            ({"highway": "primary", "maxspeed": "60"}, 60.0, "km/h", False, False),
            ({"highway": "primary", "maxspeed": "80 km/h"}, 80.0, "km/h", False, False),
            # Numeric mph
            ({"highway": "primary", "maxspeed": "30 mph"}, 48.3, "mph", False, False),
            # String literals
            ({"highway": "residential", "maxspeed": "walk"}, 5.0, "km/h", False, False),
            ({"highway": "motorway", "maxspeed": "none"}, None, "km/h", False, True),
            ({"highway": "motorway", "maxspeed": "signals"}, None, "km/h", True, False),
            ({"highway": "trunk", "maxspeed": "variable"}, None, "km/h", True, False),
        ]
        for tags, exp_speed, exp_unit, exp_var, exp_unl in table:
            with self.subTest(tags=tags):
                road = normalize_road_tags(tags, way_id=104)
                self.assertIsNotNone(road.speed_limit)
                assert road.speed_limit is not None
                self.assertEqual(road.speed_limit.speed_kmh, exp_speed)
                self.assertEqual(road.speed_limit.unit, exp_unit)
                self.assertEqual(road.speed_limit.is_variable, exp_var)
                self.assertEqual(road.speed_limit.is_unlimited, exp_unl)
                self.assertEqual(road.raw_tags, tags)

    def test_maxspeed_source_attribution(self) -> None:
        tags = {"highway": "primary", "maxspeed": "50", "source:maxspeed": "BR:urban"}
        road = normalize_road_tags(tags, way_id=105)
        self.assertIsNotNone(road.speed_limit)
        assert road.speed_limit is not None
        self.assertEqual(road.speed_limit.source, "BR:urban")
        self.assertEqual(road.speed_limit.speed_kmh, 50.0)

    def test_barriers_fixtures(self) -> None:
        table = [
            ({"highway": "service", "barrier": "gate"}, "gate", True),
            ({"highway": "track", "barrier": "bollard"}, "bollard", True),
            ({"highway": "residential", "barrier": "lift_gate"}, "lift_gate", True),
            ({"highway": "primary"}, None, False),
        ]
        for tags, exp_type, exp_barrier in table:
            with self.subTest(tags=tags):
                road = normalize_road_tags(tags, way_id=106)
                if exp_barrier:
                    self.assertIsNotNone(road.barrier)
                    assert road.barrier is not None
                    self.assertEqual(road.barrier.barrier_type, exp_type)
                    self.assertTrue(road.barrier.is_barrier)
                else:
                    self.assertIsNone(road.barrier)

    def test_conditional_rules_fixtures(self) -> None:
        tags = {
            "highway": "primary",
            "maxspeed": "60",
            "maxspeed:conditional": "30 @ (07:00-19:00)",
            "access:conditional": "no @ wet",
        }
        road = normalize_road_tags(tags, way_id=107)
        self.assertEqual(len(road.conditionals), 2)
        rules = {c.attribute: c for c in road.conditionals}
        self.assertIn("maxspeed", rules)
        self.assertEqual(rules["maxspeed"].value, "30")
        self.assertEqual(rules["maxspeed"].condition, "(07:00-19:00)")
        self.assertEqual(rules["maxspeed"].raw_tag, "maxspeed:conditional=30 @ (07:00-19:00)")

        self.assertIn("access", rules)
        self.assertEqual(rules["access"].value, "no")
        self.assertEqual(rules["access"].condition, "wet")

    def test_unknown_not_allowed_by_default(self) -> None:
        # Unknown highway
        with self.assertRaises(RoadNormalizationError):
            normalize_road_tags({"highway": "space_corridor"}, way_id=1)

        # Missing highway
        with self.assertRaises(RoadNormalizationError):
            normalize_road_tags({"name": "No Highway"}, way_id=2)

        # Unknown oneway value
        with self.assertRaises(RoadNormalizationError):
            normalize_road_tags({"highway": "primary", "oneway": "sideways"}, way_id=3)

        # Unknown access value
        with self.assertRaises(RoadNormalizationError):
            normalize_road_tags({"highway": "primary", "access": "aliens_only"}, way_id=4)

        # Unknown maxspeed value
        with self.assertRaises(RoadNormalizationError):
            normalize_road_tags({"highway": "primary", "maxspeed": "warp_speed"}, way_id=5)

        # Unknown barrier value
        with self.assertRaises(RoadNormalizationError):
            normalize_road_tags({"highway": "primary", "barrier": "laser_grid"}, way_id=6)

    def test_unknown_retained_when_allowed(self) -> None:
        tags = {
            "highway": "maglev_lane",
            "oneway": "alternating",
            "access": "secret_society",
            "maxspeed": "fast",
            "barrier": "forcefield",
            "name": "SciFi Road",
        }
        road = normalize_road_tags(tags, way_id=999, allow_unknown=True)
        self.assertEqual(road.highway_class, HighwayClass.OTHER)
        self.assertEqual(road.general_access, AccessLevel.UNKNOWN)
        self.assertIn("highway", road.unknowns)
        self.assertEqual(road.unknowns["highway"], "maglev_lane")
        self.assertIn("oneway", road.unknowns)
        self.assertEqual(road.unknowns["oneway"], "alternating")
        self.assertIn("access", road.unknowns)
        self.assertEqual(road.unknowns["access"], "secret_society")
        self.assertIn("maxspeed", road.unknowns)
        self.assertEqual(road.unknowns["maxspeed"], "fast")
        self.assertIn("barrier", road.unknowns)
        self.assertEqual(road.unknowns["barrier"], "forcefield")

        # Raw tags are preserved unaltered
        self.assertEqual(road.raw_tags, tags)

    def test_highway_link_detection(self) -> None:
        road = normalize_road_tags({"highway": "motorway_link"}, way_id=108)
        self.assertEqual(road.highway_class, HighwayClass.MOTORWAY)
        self.assertTrue(road.is_link)

        road_trunk = normalize_road_tags({"highway": "trunk"}, way_id=109)
        self.assertEqual(road_trunk.highway_class, HighwayClass.TRUNK)
        self.assertFalse(road_trunk.is_link)

    def test_to_dict_serialization(self) -> None:
        tags = {
            "highway": "primary",
            "oneway": "yes",
            "maxspeed": "50",
            "maxspeed:conditional": "30 @ school",
        }
        road = normalize_road_tags(tags, way_id=110)
        d = road.to_dict()
        self.assertEqual(d["way_id"], 110)
        self.assertEqual(d["highway_class"], "primary")
        self.assertEqual(d["oneway"], 1)
        self.assertEqual(d["access"]["general"], "public")
        self.assertEqual(d["speed_limit"]["speed_kmh"], 50.0)
        self.assertEqual(len(d["conditionals"]), 1)
        self.assertEqual(d["raw_tags"], tags)


if __name__ == "__main__":
    unittest.main()
