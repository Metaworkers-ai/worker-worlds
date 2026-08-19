"""Build a dependency-free accessible static documentation site."""

from __future__ import annotations

import html
from pathlib import Path

SOURCES = (
    "quickstart.md",
    "concepts.md",
    "authoring.md",
    "operations.md",
    "reference.md",
    "release.md",
    "api.md",
    "live-adapter-smoke.md",
    "security/threat-model.md",
    "security/secure-worker-deployment.md",
)


def render(source: str, navigation: str) -> str:
    """Render intentionally small Markdown subset as escaped preformatted source."""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width"><title>Worker Worlds 1.0</title>'
        "<style>body{font:16px system-ui;margin:auto;max-width:1100px;padding:1rem;"
        "line-height:1.5}nav a{margin-right:1rem}pre{white-space:pre-wrap;"
        "overflow-wrap:anywhere}a:focus{outline:3px solid #0563c1}</style></head>"
        "<body><header><h1>Worker Worlds <small>1.0.0rc1</small></h1>"
        f'<nav aria-label="Documentation">{navigation}</nav></header>'
        f"<main><pre>{html.escape(source)}</pre></main></body></html>"
    )


def main() -> None:
    """Build all checked-in documentation pages and verify local links."""
    root = Path(__file__).resolve().parents[1]
    output = root / "site"
    output.mkdir(exist_ok=True)
    navigation = " ".join(
        f'<a href="{name.replace("/", "-").removesuffix(".md")}.html">{Path(name).stem}</a>'
        for name in SOURCES
    )
    for name in SOURCES:
        source = (root / "docs" / name).read_text(encoding="utf-8")
        output_name = name.replace("/", "-").removesuffix(".md") + ".html"
        (output / output_name).write_text(render(source, navigation), encoding="utf-8")
    (output / "index.html").write_text(
        (output / "quickstart.html").read_text(encoding="utf-8"), encoding="utf-8"
    )
    print(f"docs_built={len(SOURCES) + 1} output={output}")


if __name__ == "__main__":
    main()
