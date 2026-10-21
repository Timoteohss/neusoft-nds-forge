import json
import tempfile
import unittest
from pathlib import Path

from nds_forge.layers.bmd_codec import tile_for, tile_origin
from nds_forge.layers.route_aux import compile_route_aux


class RouteAuxPolicyTests(unittest.TestCase):
    def test_empty_selection_requires_explicit_experimental_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "main"
            source.mkdir()
            tile = tile_for(-49.2, -25.4, 13)
            x, y = tile_origin(tile, 13)
            link = {"points": [[x, y], [x + 16, y + 16]], "attrs": {},
                    "retain_aux_geometry": False, "boundary_adjacent": False, "curved": False}
            (source / "route-model.json").write_text(json.dumps({
                "schema_version": 1, "profile_id": "neusoft-curitiba-v9",
                "tiles": [{"tile_id": tile, "links": [link]}]}))
            (source / "route-manifest.json").write_text(json.dumps({"tiles": [{"id": tile}]}))
            profile = {"profile_id": "neusoft-curitiba-v9", "level": 13, "shift": 4}
            with self.assertRaisesRegex(ValueError, "requires explicit opt-in"):
                compile_route_aux(source, root / "default", profile)
            compile_route_aux(source, root / "opted-in", {
                **profile, "empty_aux_policy": "retain_first_experimental"})
            report = json.loads((root / "opted-in/aux-manifest.json").read_text())
            self.assertEqual(report["counts"]["experimental_fallback_tiles"], 1)
            self.assertEqual(report["experimental_fallback_tile_ids"], [tile])
            self.assertEqual(report["validation_status"], "unvalidated")
