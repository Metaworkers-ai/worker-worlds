# Maintainer release guide

Worker Worlds follows semantic versioning. Additive optional v1 fields may ship
in 1.x; removals, reinterpretations, or new required fields require schema major
2. Deprecations remain documented for at least one minor release.

Run `make verify`, clean-install both artifacts, generate checksums and SBOM,
execute the release demonstration, and audit leases before tagging. Published
artifacts are immutable. Roll back by documenting a safe version pin and yanking
only the defective distribution; never overwrite a release. Publication requires
maintainer approval and is intentionally outside automated local validation.

## TestPyPI publication sequence (authorization required)

Local preparation never uploads anything:

```bash
python -m build
python -m twine check dist/worker_worlds-1.0.0rc1*
python -m twine upload --repository testpypi dist/worker_worlds-1.0.0rc1*
python3.12 -m venv /tmp/worker-worlds-testpypi
/tmp/worker-worlds-testpypi/bin/pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ worker-worlds==1.0.0rc1
/tmp/worker-worlds-testpypi/bin/worker-worlds doctor
```

Locate packaged resources without a source checkout:

```bash
scenario_dir=$(/tmp/worker-worlds-testpypi/bin/python -c \
  'import sys; print(sys.prefix + "/share/worker-worlds/scenarios")')
/tmp/worker-worlds-testpypi/bin/worker-worlds scenario validate "$scenario_dir"
/tmp/worker-worlds-testpypi/bin/worker-worlds run "$scenario_dir/refund__partial__happy.yaml" \
  --worker stub --world postgres --output /tmp/worker-worlds-first-run
```

If a candidate is defective, do not replace its files. Yank that exact candidate
in the package index, publish a higher release-candidate version, document the
reason in the changelog, and tell testers to pin the last known-good version.
