from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .pipeline import PipelineError, plan, run, status, validate_step


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nds-forge", description="Pipeline OSM para mapas Neusoft")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run", "resume", "status", "validate", "capabilities"):
        cmd = commands.add_parser(name)
        if name != "capabilities":
            cmd.add_argument("--config", type=Path, required=True)
        if name in {"plan", "run", "resume"}:
            cmd.add_argument("--target", help="Sobrescreve pipeline.target do TOML")
        if name in {"run", "resume"}:
            cmd.add_argument("--allow-network", action="store_true",
                             help="Autoriza download explicitamente; URL ainda exige SHA configurado")
            cmd.add_argument("--allow-experimental", action="store_true")
        if name == "validate":
            cmd.add_argument("--step", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "capabilities":
            from .registry import all_steps
            result = {"capabilities": [
                {"id": step.id, "status": step.status,
                 "description": step.description,
                 "validation_required": True,
                 "device_execution_default": False}
                for step in all_steps()]}
        else:
            config = load_config(args.config)
            target = getattr(args, "target", None) or config.pipeline.get("target", "source.verify")
        if args.command == "plan":
            result = plan(config, target)
        elif args.command == "run":
            result = run(config, target, args.allow_network, args.allow_experimental)
        elif args.command == "resume":
            result = run(config, target, args.allow_network, args.allow_experimental, resume=True)
        elif args.command == "status":
            result = status(config)
        elif args.command == "validate":
            result = validate_step(config, args.step)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (ConfigError, PipelineError, OSError) as exc:
        print(f"nds-forge: erro: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
