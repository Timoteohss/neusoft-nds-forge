#!/usr/bin/env python3
"""Read/write the observed ZV-zlib SQLite container using a caller key.

The key is read into process memory and is never printed or serialized. This
CLI does not discover keys, update metadata, or modify its input file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from nds_forge.layers.container import pack_sqlite, unpack_sqlite
except ImportError as exc:  # helpful when run from a source checkout
    raise SystemExit("Install nds-forge (`pip install -e '.[container]'`) before using this tool") from exc


def _key(path: Path) -> bytes:
    key = path.read_bytes()
    if len(key) != 16:
        raise ValueError("Key file must contain exactly 16 raw bytes")
    return key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("pack", "unpack"):
        sub = commands.add_parser(name)
        sub.add_argument("source", type=Path, help="SQLite input for pack; ZV-zlib input for unpack")
        sub.add_argument("output", type=Path, help="New output path; existing files are never overwritten")
        sub.add_argument("--key-file", type=Path, required=True,
                         help="Caller-provided 16-byte raw key file (never logged)")
    args = parser.parse_args(argv)
    try:
        key = _key(args.key_file)
        result = (pack_sqlite if args.command == "pack" else unpack_sqlite)(
            args.source, args.output, key)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"nds-container: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"command": args.command, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
