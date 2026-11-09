---
name: product-contract
description: >-
  Internal reference loaded by product-plan and product-build, and before validating an assigned product contract at dispatch, promotion, relaunch, ready or landing. Own shared source integrity, exact approval, task binding and evidence predicates, not a separately selectable planning or build workflow.
user-invocable: false
metadata:
  internal: true
---

# Product contract - internal reference

[Product Plan](../product-plan/SKILL.md) and [Product Build](../product-build/SKILL.md) are the two active user workflows.
This reference retains their shared deterministic engine and lifecycle invariants; it is not a third workflow.
Load only the relevant references for the current phase rather than running another operating sequence here.

## Shared owners

- [Sources and interrogation](references/interrogation.md) and [source extraction](references/extraction.md) own exact originals, question/answer provenance and complete source dispositions.
- [Specification](references/specification.md), [proportionality](references/proportionality.md) and [work slicing](references/slicing.md) own canonical specification, compact/full presentation and approved assignments.
- [Acceptance](references/acceptance.md) owns independent reverse-source audit and acceptance custody.
- [Lifecycle integration](references/lifecycle.md) owns dispatch, promotion, relaunch, ready, landing and retained-history admission.
- [Evidence and authority](references/evidence.md) owns attributable observations, budgets and distinct acceptance/delivery predicates.
- [Continuous improvement](references/improvement.md) owns confirmed-gap capture and deliberate SOP revisions.
- [Interface reference](references/interfaces.md) points to the executable command and schema owners, including the existing build, Graphify and report interfaces.

Use these predicates for applicable new implementation; there is no shadow, advisory or optional enforcement mode.
Already running unmarked work retains its existing delivery obligations without gaining a product-acceptance certificate.

## Privacy and authority

All originals, source chains, approved manifests, revisions, observations and corrections remain under the explicit owning `FM_HOME` independently of worker cleanup.
`inspect`, `render`, and `instructions` are private outputs, not publication commands.
No part of this reference grants push, publication, external tests, merge, destructive action, or cross-home inspection authority.
Sensitive projections are separate reviewed artifacts and can never replace or edit private originals.

## Provenance

[Attribution](references/attribution.md) records the verified design sources.
This is a fresh native implementation of their useful concepts, not a copied orchestrator or installer.
