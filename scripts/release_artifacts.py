"""Generate deterministic checksums, an SBOM, and local build provenance."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

from worker_worlds import USER_AGENT, __version__


def main() -> None:
    """Describe built distributions without publishing them."""
    dist = Path("dist")
    generated = {"SHA256SUMS", "sbom.cdx.json", "provenance.json"}
    artifacts = sorted(
        path for path in dist.iterdir() if path.is_file() and path.name not in generated
    )
    checksums = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in artifacts
    )
    (dist / "SHA256SUMS").write_text(checksums, encoding="utf-8")
    components = [
        {"type": "library", "name": item.metadata["Name"], "version": item.version}
        for item in sorted(
            importlib.metadata.distributions(), key=lambda value: value.metadata["Name"].lower()
        )
        if item.metadata["Name"]
    ]
    sbom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {"type": "application", "name": "worker-worlds", "version": __version__}
        },
        "components": components,
    }
    (dist / "sbom.cdx.json").write_text(
        json.dumps(sbom, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    provenance = {
        "generated_at": datetime.now(UTC).isoformat(),
        "builder": "scripts/release_artifacts.py",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "inputs": [path.name for path in artifacts],
        "publication": "not performed",
        "package_version": __version__,
        "user_agent": USER_AGENT,
    }
    (dist / "provenance.json").write_text(
        json.dumps(provenance, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(f"release_artifacts={len(artifacts)}")


if __name__ == "__main__":
    main()
