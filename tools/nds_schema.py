#!/usr/bin/env python3
"""Export DDL-only contracts or create empty SQLite databases, entirely offline."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from nds_forge.schema_contract import create_empty_database, export_contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "create"):
        sub = commands.add_parser(name)
        sub.add_argument("source", type=Path)
        sub.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "export":
        contract = export_contract(args.source)
        with args.output.open("x", encoding="utf-8") as dest:
            json.dump(contract, dest, ensure_ascii=False, indent=2)
            dest.write("\n")
        result = {"exported_objects": len(contract["objects"]), "exported_rows": 0}
    else:
        result = create_empty_database(json.loads(args.source.read_text()), args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
