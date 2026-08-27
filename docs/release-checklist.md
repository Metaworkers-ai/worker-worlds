# v1.0.0rc1 release checklist

| Area | Status | Evidence / limitation |
|---|---|---|
| Functional completeness | Ready locally | 225 live-ready commerce/supply-chain/insurance scenarios plus ten stub demonstrations; specialized mutation matrices pass |
| Contract/schema compatibility | Ready | v1 fixtures and drift check |
| Database migrations | Ready | empty-database migration and checksums |
| Determinism | Ready | canonical fixtures, snapshots, replay tests |
| Isolation | Ready | per-run schema, lease, ten-run test |
| Security/containment | Ready locally | world/database controls verified; process, host, network, and credentials are explicit deployment boundaries |
| Adapter compatibility | Ready | deterministic native construction/conformance |
| Grading correctness | Ready | pure assertions/policies and incomplete-evidence fail closed |
| Comparison correctness | Ready | critical regression and benign-change demonstrations |
| Scenario quality | Pending external | 225 self-contained live prompts require tool-result evidence; independent domain-owner sign-off and a post-hardening paid replay remain required |
| Performance | Ready locally | final-tree 200-scenario and reset benchmarks captured without hosted claims |
| CLI usability | Ready locally | JSON, quiet, verbose, dry-run, overwrite, redaction, and exit behavior tested |
| Documentation | Ready | user, author, operations, security, and release guides |
| Packaging | Ready locally | wheel/sdist, metadata, resources, clean installs, rehearsal, and reproducible hashes pass |
| CI | Pending external | local workflow contracts and `make verify` pass; hosted checks on `agent/saas-dashboard` must pass before merge to `main` |
| Licensing/governance | Ready | MIT, contribution, conduct, security, support policy |
| Rollback/support | Ready | immutable release and version-pin/yank guidance |

One explicitly authorized pre-hardening OpenAI insurance run exposed prompt/inaction weaknesses and
is not a release result. A post-hardening live replay, TestPyPI installation, hosted documentation,
signed tags, GitHub releases, and PyPI publication remain **PENDING EXTERNAL VALIDATION**.
Publication is never performed by local release validation.
