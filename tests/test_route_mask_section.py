"""Byte-level tests for the transition-mask section of the ROUTE encoder.

The expected bytes come from a fixture accepted by the installed Neusoft
RoutingTile reader (tile 540413880, section-8 offset 769); see
`geely-ex2-investigation/.../flex-writer-validated.json`.
"""
from __future__ import annotations

import unittest

from nds_forge.layers.bmd_codec import tile_origin
from nds_forge.layers.route_codec import (
    MASK_DESCRIPTOR_BYTES,
    encode_mask_section,
    encode_route_tile,
    parse_main_header,
)

EXPECTED = bytes.fromhex("0101000800000030a000800004000100050002")


class RouteMaskSectionTests(unittest.TestCase):
    def test_mask_section_matches_native_accepted_bytes(self) -> None:
        section = encode_mask_section(769, [(4, 0x0001), (5, 0x0002)])
        self.assertEqual(section, EXPECTED)
        single = encode_mask_section(769, [(4, 1)])
        self.assertEqual(len(single), MASK_DESCRIPTOR_BYTES + 6)

    def test_mask_outside_sixteen_bits_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            encode_mask_section(0, [(0, 1 << 16)])

    def test_header_marks_section_eight_and_offsets_are_ordered(self) -> None:
        blob = self._tile([(0, 0xFFFF)])
        offsets = parse_main_header(blob)["offsets"]
        self.assertTrue(offsets[8] and offsets[9])
        self.assertLess(offsets[1], offsets[2])
        self.assertLess(offsets[2], offsets[8])
        self.assertLess(offsets[8], offsets[9])
        section = blob[offsets[8]:offsets[9]]
        self.assertEqual(section[0], 1)                  # one descriptor
        self.assertEqual(section[1], 1)                  # one attribute id
        self.assertEqual(int.from_bytes(section[2:4], "big"), 8)
        group_offset = (((section[4] & 0x0F) << 28) | (section[5] << 20)
                        | (section[6] << 12) | (section[7] << 4) | (section[8] >> 4))
        self.assertEqual(group_offset, offsets[8] + MASK_DESCRIPTOR_BYTES)
        self.assertEqual(blob[group_offset:group_offset + 2], b"\x00\x40")

    def test_without_masks_section_eight_is_absent(self) -> None:
        blob = self._tile(None)
        self.assertEqual(parse_main_header(blob)["offsets"][8], 0)

    @staticmethod
    def _tile(masks):
        ox, oy = tile_origin(0x20100000, 13)
        nodes = {0: (ox, oy), 1: (ox + 1000, oy), 2: (ox, oy + 1000)}
        links = [{"attrs": {"length_raw": 100}}, {"attrs": {"length_raw": 100}}]
        node_links = {0: [(False, None, 0, 1), (False, None, 1, 1)],
                      1: [(False, None, 0, 0)],
                      2: [(False, None, 1, 0)]}
        return encode_route_tile(0x20100000, 13, 0, nodes, links, node_links,
                                 {}, [], masks)


if __name__ == "__main__":
    unittest.main()