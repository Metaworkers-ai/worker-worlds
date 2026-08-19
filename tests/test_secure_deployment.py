from pathlib import Path

import yaml


def test_secure_worker_compose_has_required_local_controls() -> None:
    path = Path("examples/secure-worker/compose.yaml")
    data = yaml.safe_load(path.read_text())
    worker = data["services"]["worker"]
    assert worker["read_only"] is True
    assert worker["user"] != "0:0"
    assert worker["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in worker["security_opt"]
    assert worker["pids_limit"] <= 64 and worker["mem_limit"] == "512m"
    assert "/var/run/docker.sock" not in str(worker)
    assert data["networks"]["worker_isolated"]["internal"] is True
    assert "POSTGRES" not in str(worker["environment"])
