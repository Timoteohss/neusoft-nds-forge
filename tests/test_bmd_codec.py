"""Unit tests for BMD codec polyline delta encoding and decoding."""
from __future__ import annotations

import itertools
import unittest

from nds_forge.layers.bmd_codec import (
    decode_polyline,
    encode_polyline,
    encode_road_tile,
)


class BmdPolylineCodecTests(unittest.TestCase):
    def test_name_association_overflow_is_rejected_before_encoding(self) -> None:
        # Empty points are deliberate: the count guard must run before the
        # geometry encoder, which would otherwise reject this placeholder.
        with self.assertRaisesRegex(ValueError, "name association count exceeds 32767"):
            encode_road_tile([([], 4, 10001)] * 32768, 11, 4, 142209789)

    def test_encode_decode_roundtrip_basic(self) -> None:
        origin = (0, 0)
        shift = 4
        coord_bits = 16
        points = [(100 << shift, 200 << shift), (150 << shift, 250 << shift), (120 << shift, 300 << shift)]
        encoded = encode_polyline(points, coord_bits, shift, origin)
        decoded, _ = decode_polyline(encoded.bytes(), 0, coord_bits, shift, origin)
        self.assertEqual(points, decoded)

    def test_encode_large_delta_subdivides_cleanly(self) -> None:
        origin = (0, 0)
        shift = 4
        coord_bits = 16
        # Delta of 50000 exceeds signed 16-bit range (-32768..32767)
        points = [(0, 0), (50000 << shift, 40000 << shift)]
        encoded = encode_polyline(points, coord_bits, shift, origin)
        decoded, _ = decode_polyline(encoded.bytes(), 0, coord_bits, shift, origin)
        # Endpoints are preserved exactly
        self.assertEqual(decoded[0], points[0])
        self.assertEqual(decoded[-1], points[-1])
        # Intermediate points were created
        self.assertGreater(len(decoded), 2)
        # All decoded segments have deltas fitting in signed 16-bit range
        for (x1, y1), (x2, y2) in itertools.pairwise(decoded):
            dx = (x2 - x1) >> shift
            dy = (y2 - y1) >> shift
            self.assertLessEqual(abs(dx), 32767)
            self.assertLessEqual(abs(dy), 32767)
