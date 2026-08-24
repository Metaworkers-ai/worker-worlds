"""Generate and verify the checked versioned HTTP OpenAPI document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from worker_worlds.api import create_app

OPENAPI_PATH = Path("schemas/v1/openapi.json")


def generated_openapi() -> str:
    """Return deterministic OpenAPI JSON for the v1 control plane."""
    return json.dumps(create_app().openapi(), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Generate or check the checked OpenAPI artifact."""
    parser = argparse.ArgumentParser(prog="worker-worlds-openapi")
    parser.add_argument("command", choices=("generate", "check"))
    args = parser.parse_args(argv)
    content = generated_openapi()
    if args.command == "generate":
        OPENAPI_PATH.parent.mkdir(parents=True, exist_ok=True)
        OPENAPI_PATH.write_text(content, encoding="utf-8")
        print(f"generated {OPENAPI_PATH}")
        return 0
    if not OPENAPI_PATH.is_file() or OPENAPI_PATH.read_text(encoding="utf-8") != content:
        print(f"OpenAPI drift detected: {OPENAPI_PATH}")
        return 1
    print(f"OpenAPI current: {OPENAPI_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
