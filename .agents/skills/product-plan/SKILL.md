---
name: product-plan
description: >-
  Plan a product, app, feature or consequential change before building it. Use when the user invokes /product-plan, wants to turn an idea or questionnaire into a specification, supplies requirements to plan, or wants to prepare an approved build for later. Own intake through approved private build-artifact preparation, never implementation execution.
user-invocable: true
metadata:
  internal: true
---

# Product Plan

Prepare an approved build now; leave execution for an explicit later `/product-build` request.
This workflow owns planning, not a second contract format or implementation orchestrator.
Load [the shared product-contract reference](../product-contract/SKILL.md) before preparing records.

## Intake and specification

1. Resolve the project, owning home and any existing parent contract through normal Firstmate intake.
   Consult existing reports before commissioning research; route project investigation and planning to the fitting second mate or planning worker rather than doing it as firstmate.
   A supplied implementation request enters planning when it has no current approved artifact; it does not approve an unseen generated specification or authorize this workflow to build.
2. Follow [sources and interrogation](../product-contract/references/interrogation.md) and [source extraction](../product-contract/references/extraction.md).
   Import complete originals and already answered questions before synthesis; ask only genuinely unresolved decisions, not a replacement questionnaire.
3. Have the planner generate the canonical specification and readable view under [specification](../product-contract/references/specification.md), [proportionality](../product-contract/references/proportionality.md) and [work slicing](../product-contract/references/slicing.md).
   Prepare bounded vertical units, dependency-ready parallel work and an explicit integration assignment, with inputs, interfaces, non-goals, subsystem scope, evidence boundaries, completion oracles and bounded handoff checkpoints.
   Do not put the entire specification into one oversized implementation assignment by default.
4. Prepare the private planning/project structure through [project-management](../project-management/SKILL.md) when project creation or registration is needed.
   Keep product code scaffolding and dependency changes out of planning; project initialization still needs its own concrete authority.
   Keep sources, specifications and builder artifacts private under the owning home, not in committed project memory.

## Code map, audit and approval

5. Use [the Graphify adapter](../../../bin/fm-product-graph.py)'s current help to probe and prepare a project-private baseline outside the source project and any Git working copy.
   Reuse only its verified private pin; setup requires separately recorded installation and package-network authority.
   Use the adapter's filtered local extraction, never Graphify workflow installers, hooks, hosted analysis or a global fallback.
   Retain the actual immutable baseline observation and coverage, including explicit empty, unsupported or unavailable results; do not invent topology or claim a map proves behavior.
   A missing setup permission or tool is reported honestly rather than silently substituted.
6. Obtain the independent source-first reverse audit in [acceptance](../product-contract/references/acceptance.md).
   Findings go back to the planner; the editor cannot certify its own correction.
7. Present the concise outcome, significant technical choices, exclusions, journeys, evidence and external-operation boundaries, with the full private readable view available.
   Obtain the actual owner-channel approval of the exact audited manifest digest and record the response verbatim through the shared engine.
   Unresolved decisions follow [captain-hold-lifecycle](../captain-hold-lifecycle/SKILL.md); approval of planning, a roster or a tool install is not approval of an unseen manifest.

## Automatic builder handoff, then stop

8. After exact approval, automatically emit the immutable private build artifact through [the existing build interface](../../../bin/fm-product-build.py)'s `emit` operation.
   It carries the current contract, exact work roster, project, actual baseline observation and explicitly resolved delivery selection from normal Firstmate intake.
   The executable's help owns the payload; do not hand-write another builder contract or ask the owner to maintain JSON or traceability matrices.
   Replay with the same immutable identity after interruption; verify the artifact before reporting it available.
9. Return a concise specification outcome, the durable build identifier/artifact location, any disclosed map limitations and the next invocation: `/product-build` with that identifier.
   Stop here even if workers could start immediately.
   Do not reserve or launch a build secondmate, allocate implementation workers, dispatch a wave, edit product code, push or merge during Product Plan.

If planning is interrupted, resume the exact owning-home source chain, draft, audit, approval and artifact records through the shared interfaces, not conversation memory.
A changed product ask returns to source intake and exact revised approval; [the lifecycle reference](../product-contract/references/lifecycle.md) owns affected-task admission.
Confirmed specification gaps follow [continuous improvement](../product-contract/references/improvement.md), not silent shared-tool updates.
