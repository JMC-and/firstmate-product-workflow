# Extraction boundary and dependency inventory

## Copied material

`inventory.json` lists every copied file, its upstream path, mode, packaged SHA-256 and any adaptation from Firstmate commit `733763dd844b8101fc6f55ee6abb1a442ff19173`.
The inventory includes the MIT license, three skill entry points, eleven contract references, six executables/libraries and eight focused test files.
No production source code, skill semantics, schema, pin or approval predicate was adapted. One build test omits upstream prompt-prose assertions while retaining discovery metadata coverage.
New packaging-owned files are `README.md`, this document, `inventory.json`, `.gitignore` and `scripts/package.py`.
No Home records, credentials, lead-generation skill or unrelated Firstmate internals are redistributed.
The generic contract regression retains an upstream synthetic leads-themed negative-evidence replay; it contains no business data, scraper or lead-generation workflow.
The two upstream live Pi tests are deliberately omitted: this package's validation neither spends model tokens nor exercises live replacement sessions.

## Required Firstmate surfaces (not vendored)

Installation overlays the pinned full Firstmate checkout so sibling paths and relative links keep their original meanings.
Reading the extracted skills in isolation leaves the following sibling skill links unresolved by design; the installed checkout resolves them:

- `project-management`: owning-home intake and project registration.
- `captain-hold-lifecycle`: unresolved owner decisions and completion accounting.
- `secondmate-provisioning`: dedicated owner setup and normal parent routing.
- `harness-adapters`: harness-specific provisioning and dispatch.
- `ship-landing`: validation custody, authorized landing and cleanup.

Direct executable integration dependencies remain in Firstmate's `bin/`:

- `fm-dod-lib.sh`: authoritative task heading/context projection.
- `fm-secondmate-parent-lib.sh`: exact parent record parsing.
- `fm-parent-channel-lib.sh`: escalation destination and append-once delivery.
- `fm-spawn.sh`: worker dispatch and capacity admission.
- `fm-control.sh`: worker stop, recovery and fresh-context replacement.
- `fm-send.sh`: task notifications.
- `fm-merge-local.sh`: parent-owned guarded local delivery.

Firstmate itself integrates the included contract shell adapter into brief, spawn, promotion, ready, relaunch and landing gates, and integrates report polling into its existing supervisor loop.
Those call sites, its role/consent policy, home seeding, backend selection, task metadata, worktree pool, fleet synchronization and validation/delivery tooling are still Firstmate-owned, not reimplemented here.
Their transitive dependencies remain the full pinned Firstmate installation's responsibility.
Copying only these six scripts somewhere else does not recreate enforcement or execution.

Focused tests additionally use Firstmate's `tests/lib.sh`, `tests/fixtures.sh` and their transitive fixtures, plus backend/control/brief/home-seed/merge/fleet-sync entry points.
The test command reads a committed tree into an isolated temporary fixture instead of silently vendoring these dependencies into the public package.
It gives the fixture a synthetic local Git root so role/lineage checks cannot borrow this repository's history.
All fixtures are deleted on exit; only synthetic local Git repositories and fake endpoints are exercised.

## Runtime dependencies

- POSIX/macOS or Linux, Bash, Git and `python3` with standard library including `fcntl`; no pip dependency for the contract/build/report engines.
- Python 3.12+ for the new package command's safe tar extraction; the pinned upstream workflow remains unmodified and is tested with host `python3`.
- A configured Firstmate Home and supported harness/backend already installed through Firstmate's own setup; this package adds no runtime compatibility layer.
- Current report interface: Herdr CLI and an attached focused owner-visible terminal, driven only through normal authorized Firstmate use.
  Offline tests inject a fake CLI; no live lifecycle operation is part of package validation.
- Optional Graphify capability: `uv`, an existing suitable Python interpreter, separately authorized PyPI access for setup, and `graphifyy==0.9.102` plus its resolver-hashed transitive dependencies in the private per-project environment.
  The adapter records the resolved dependency inventory and installed metadata locally; none of those packages are bundled here.
  Extraction needs macOS `sandbox-exec` or Linux user/network `unshare`; unavailable isolation is an explicit unsupported result.
- Existing selected delivery path may require Firstmate's configured no-mistakes and forge tools; this repository does not install or weaken them.

## Validation limits

Offline tests prove persisted source integrity, exact audit/approval, task admission, evidence/budget custody, build ownership/recovery/integration, privacy refusals and reporting behavior through public interfaces.
They do not certify live model quality, live Herdr attachment, live Graphify installation, arbitrary runtimes, or a real product's acceptance.
No CI, remote publication, hook installer, automatic updater or generalized adapter platform is added by this extraction.
