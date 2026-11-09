# Product Plan + Product Build

A shareable, unchanged snapshot of Firstmate's two-stage product workflow: plan and approve first, explicitly build later.
Includes the deterministic Product Contract engine, private Graphify adapter, report interface, references and focused tests.
This is a Firstmate overlay, **not a standalone orchestrator or a portable skills.sh package**.

## Install and compatibility

Use a configured [Firstmate](https://github.com/Bytes-Brands/firstmate) checkout at **`733763dd844b8101fc6f55ee6abb1a442ff19173`**.
That exact revision is the only supported boundary; arbitrary versions, harnesses and runtime backends are not supported by this extraction.
The workflow is already present there; installation is an idempotent overlay for sharing and reviewing its owned surfaces, not an upgrade or a new opt-in mechanism.
Keep the complete Firstmate checkout and its existing setup, policy and private owning Home.

Clone this repository to a separate directory, then run from its root:

```sh
python3.12 scripts/package.py install /absolute/path/to/firstmate
```

Package tooling requires Python 3.12+; workflow commands use Firstmate's `python3`, Bash and Git.
The installer checks the exact clean revision and inventory hashes, copies only the workflow files, and does not edit Home, hook configuration or runtime selection.
Do not install the skill folders alone: relative references and executable lifecycle dependencies require the Firstmate layout.
See [dependencies](docs/dependencies.md) and the per-file [source inventory](inventory.json).

## Use

1. In the configured Firstmate session, invoke `/product-plan` with the project and existing idea, questionnaire or requirements.
   It retains private sources, prepares the specification, obtains independent audit and exact owner approval, emits a build artifact, then stops without implementation.
2. Later explicitly invoke `/product-build` with that build identifier.
   Firstmate verifies the artifact, provisions the dedicated secondmate, establishes reporting, executes bounded slices and verifies the integrated result.

The installed [Product Plan](.agents/skills/product-plan/SKILL.md) and [Product Build](.agents/skills/product-build/SKILL.md) are the procedure owners.
The internal [Product Contract reference](.agents/skills/product-contract/SKILL.md) is shared machinery, not a third planning process.
Executable `--help` owns payloads and commands:

```sh
bash /absolute/path/to/firstmate/bin/fm-product-contract.sh --help
python3 /absolute/path/to/firstmate/bin/fm-product-build.py --help
python3 /absolute/path/to/firstmate/bin/fm-product-graph.py --help
python3 /absolute/path/to/firstmate/bin/fm-product-report.py --help
```

## Safety and approval

Planning approval does not authorize implementation, publication, push, merge, destructive operations or external tests.
Keep originals, contracts, maps, evidence and reports private under the explicit owning Home; never commit them here.
Graphify setup requires separately granted install and package-network authority; extraction requires local network isolation and never falls back to hosted analysis.
The current visible report sink requires Herdr; unavailable reporting blocks build start rather than silently substituting a file.
Existing Firstmate role, consent, dispatch and landing gates remain authoritative.

## Focused validation

```sh
python3.12 scripts/package.py test /absolute/path/to/firstmate
```

This reads the pinned committed Firstmate tree into a disposable fixture, overlays the package, and runs contract, build, graph and report suites.
It needs no live model or package-install authority; live Graphify setup is intentionally skipped and reporting uses fake terminals.
Temporary storage must be outside every Git checkout; use `--scratch /absolute/existing/non-git-directory` if necessary.
The upstream shell test entry points also run directly after overlay in the pinned Firstmate checkout:

```sh
for subject in contract build graph report; do
  bash "tests/fm-product-${subject}.test.sh" || exit
done
```

## License and provenance

[MIT, copyright 2026 Kun Chen](LICENSE).
All copied workflow files are byte-for-byte unchanged from the pinned Firstmate commit, including executable modes; `inventory.json` records the single test-only assertion adaptation.
[Design attribution and retained MIT notices](.agents/skills/product-contract/references/attribution.md) credit Matt Pocock, Jesse Vincent and GitHub.
Graphify is a separately installed dependency, not redistributed here.
