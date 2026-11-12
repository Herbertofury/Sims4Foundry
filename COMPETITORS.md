# Comparable creator tools and differentiation research
Source review: **2026-11-12**.

The current README identifies **Sims4CreatorStudio** but provides no feature inventory. This page is therefore a research and evaluation backlog, not a claim of implemented editing, rendering or game compatibility.

| Reference | Scope and evidence | Proposed evaluation |
| --- | --- | --- |
| [Sims4Tools / s4pe](https://github.com/s4ptacle/Sims4Tools) | Sims 4 modding tools and resource library | Package/resource inspection, resource identifiers, unknown-data preservation and safe round trips. Verify maintenance and exact license before reuse. |
| [Sims 4 Studio](https://sims4studio.com/) | Creator-tool comparison candidate | Review official documentation for supported authoring/batch workflows, then compare representative creation tasks. Official site retrieval timed out in this pass; current release/features are **not verified here**. |

## Establish the baseline before promising superiority
- [ ] Inventory actual code, implemented workflows, supported formats and tests; document missing/incomplete paths.
- [ ] Verify the official Sims 4 Studio documentation and identify exact versions for an authorized comparison.
- [ ] Create a rights-safe small/large package corpus with known resource counts, thumbnails, meshes, textures, tuning and unsupported data.
- [ ] Compare inspection/search latency, cold indexing, memory, edit/undo/save/reopen correctness and diagnostic usefulness.
- [ ] Prove unchanged resources remain byte-identical where the format permits, and verify changed packages in the supported game build.
- [ ] Keep original assets and saves untouched; test on copies and retain rollback evidence.
- [ ] Separate this project's scope from External-CAS rather than duplicating an active implementation. No in-game fidelity or live-sync claim follows from package parsing alone.

Any local game resources or creator content used for testing stay local unless separately licensed for redistribution. A useful differentiator would be a transparent, reversible authoring workflow with precise unsupported-state reporting, not a larger list of unchecked format badges.

## Evidence and refresh rules
Review official release notes, documentation and issue trackers daily when the scheduled review runs. Record the source URL, check date, version/commit, relevant change, user impact and next experiment; retain last-good information and mark inaccessible sources. No change is a valid result. A scheduled review is not continuous monitoring.

This is a source-backed research shortlist, **not a head-to-head benchmark or a claim of feature/performance superiority**. Before recommending adoption, compare the current application and candidate on the same real workloads, machine, versions and correctness requirements. Retain raw timings, p50/p95 latency, CPU/memory, failures and rollback evidence. Check licensing and privacy before reuse; an open repository does not grant unrestricted reuse. New features remain proposals until approved, implemented and accepted.


## Candidate foundations, reuse and shared Sims tooling
Added **2026-11-12**. The sparse repository feature inventory remains a blocker to selecting a production foundation confidently.

| Candidate / mode | Concrete reuse opportunity | Gate before adoption |
| --- | --- | --- |
| [Sims4Tools](https://github.com/s4ptacle/Sims4Tools), library or differential oracle | Resource inspection and package-reading behavior | Exact license, dependency/runtime viability, unknown-resource preservation, malformed-input limits |
| [Sims 4 Studio](https://sims4studio.com/), behavior/workflow comparator | Authoring and inspection UX to evaluate after official documentation is accessible | No assumption of source availability or permission to copy proprietary assets/code; version-specific real-file tests |
| Existing project parsers and validation tools, after inventory | Reuse maintained code rather than another incompatible package implementation | Document actual supported resources, ownership, round-trip and failure evidence |

**Proposed interoperation:** [External-CAS](https://github.com/Herbertofury/External-CAS) and [Hover](https://github.com/Herbertofury/Hover-App) are candidates for shared read-only resource identities, dependency manifests and safe preview metadata. Foundry owns authoring operations; previews should not silently write packages, and an external editor's sync mode is not permission for another app to mutate game state. Extract only proven reusable modules with versioned schemas, not one giant shared application.

**Improvement objective:** reduce repeated indexing and conflicting interpretations while making edits reversible, unsupported fields explicit and creator workflows inspectable. This is a hypothesis; Rust or shared code alone does not demonstrate faster or more faithful behavior.

- [ ] Establish actual repository/module inventory and game-version support.
- [ ] Compare readers on identical authorized packages, including corruption and unknown resource types.
- [ ] Prove undo/save/reopen and unaffected-resource preservation before adding write interop.
- [ ] Benchmark cold/warm indexing, preview and edit latency plus memory against current implementations.
- [ ] Keep game/creator assets local, verify licenses separately for code and test data, and retain independent rollback.
