from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .bootstrap import BootstrapError, fetch_bundles
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
            cmd.add_argument("--progress", action="store_true",
                             help="Mostra início/fim de cada etapa em stderr")
        if name == "validate":
            cmd.add_argument("--step", required=True)
    fetch = commands.add_parser("fetch-inputs", help="Baixa bundles versionados com SHA pinado")
    fetch.add_argument("--config", type=Path, required=True)
    fetch.add_argument("--allow-network", action="store_true",
                       help="Autoriza explicitamente download dos bundles do perfil")
    build = commands.add_parser("build", help="Buscar inputs pinados se faltarem e executar o target do perfil")
    build.add_argument("--config", type=Path, required=True)
    build.add_argument("--target", help="Sobrescreve pipeline.target do TOML")
    build.add_argument("--allow-network", action="store_true",
                       help="Autoriza baixar bundles ausentes; bundles existentes são verificados offline")
    build.add_argument("--allow-experimental", action="store_true",
                       help="Autoriza explicitamente etapas experimentais do perfil")
    build.add_argument("--progress", action="store_true",
                       help="Mostra início/fim de cada etapa em stderr")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result: dict[str, Any]
    try:
        if args.command == "capabilities":
            from .registry import all_steps
            result = {"capabilities": [
                {"id": step.id, "status": step.status,
                 "description": step.description,
                 "validation_required": True,
                 "device_execution_default": False}
                for step in all_steps()]}
        elif args.command == "fetch-inputs":
            config = load_config(args.config)
            bundles = config.raw.get("downloads", {}).get("bundles", [])
            if not isinstance(bundles, list) or not bundles:
                raise BootstrapError("Config não declara downloads.bundles")
            result = fetch_bundles(config.path, bundles, args.allow_network)
        else:
            config = load_config(args.config)
            target = getattr(args, "target", None) or config.pipeline.get("target", "source.verify")
        if args.command == "fetch-inputs":
            pass
        elif args.command == "build":
            fetch_result = fetch_bundles(config.path,
                config.raw.get("downloads", {}).get("bundles", []), args.allow_network)
            target = args.target or config.pipeline.get("target", "source.verify")
            pipeline_result = run(config, target, False, args.allow_experimental,
                                  resume=True, progress=args.progress)
            result = {"fetch": fetch_result, "build": pipeline_result}
        elif args.command == "plan":
            result = plan(config, target)
        elif args.command == "run":
            result = run(config, target, args.allow_network, args.allow_experimental,
                         progress=args.progress)
        elif args.command == "resume":
            result = run(config, target, args.allow_network, args.allow_experimental,
                         resume=True, progress=args.progress)
        elif args.command == "status":
            result = status(config)
        elif args.command == "validate":
            result = validate_step(config, args.step)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except (ConfigError, PipelineError, BootstrapError, OSError) as exc:
        print(f"nds-forge: erro: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
