#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
acceptance_dir=$(mktemp -d "${TMPDIR:-/tmp}/worker-worlds-acceptance.XXXXXX")
cleanup() { rm -rf "$acceptance_dir"; }
trap cleanup EXIT INT TERM
started=$(date +%s)
python3.12 -m venv "$acceptance_dir/venv"
"$acceptance_dir/venv/bin/pip" install --disable-pip-version-check "$repo_root/dist/worker_worlds-1.0.0rc1-py3-none-any.whl" >/dev/null
install_done=$(date +%s)
cd "$acceptance_dir"
export WORKER_WORLDS_DATABASE_URL=postgresql://worker_worlds:worker_worlds_local@127.0.0.1:55432/worker_worlds_test
packaged_example="$acceptance_dir/venv/share/worker-worlds/examples/scenarios/refund_happy.yaml"
"$acceptance_dir/venv/bin/worker-worlds" migrate >/dev/null
"$acceptance_dir/venv/bin/worker-worlds" doctor >/dev/null
migrate_done=$(date +%s)
"$acceptance_dir/venv/bin/worker-worlds" run "$packaged_example" --worker stub --world postgres --output report >/dev/null
test -s report/*.json
run_done=$(date +%s)
printf 'environment_and_install_s=%s\n' "$((install_done-started))"
printf 'migration_and_doctor_s=%s\n' "$((migrate_done-install_done))"
printf 'first_report_s=%s\n' "$((run_done-started))"
