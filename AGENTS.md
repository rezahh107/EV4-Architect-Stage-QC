# AGENTS.md

## Scope

These instructions apply to the entire repository unless a closer nested `AGENTS.md` or `AGENTS.override.md` provides more specific guidance.

## Repository Role

`EV4-Architect-Stage-QC` is the official local Windows-first quality-control consumer for `rezahh107/EV4-Architect-Repo`.

It provides a Tkinter workflow for:

- selecting and verifying a compatible Architect checkout;
- replaying prefinal Stage Output history through the official Architect Runtime;
- generating deterministic attempt diagnostics and final-stage context;
- validating the model-authored `/project-gate-export` Stage Output through Architect-owned Runtime finalization.

It does not own Architect Pipeline semantics, Stage Result or Run State derivation, Payload issuance, provenance, Project Gate finalization, Handoff authority, CE acceptance, Elementor execution, Builder work, Responsive QA, deployment, or release.

## Mandatory Session Startup

Before modifying or validating the repository, read these authorities in order:

1. `README.md`
2. `STATUS.md`
3. `architect-authority.lock.json`
4. `.github/workflows/validate.yml`
5. `pyproject.toml`
6. the relevant source files and tests
7. the selected Architect checkout's `manifests/architect-runtime-authority-manifest.v1.json`
8. the selected Architect checkout's active Runtime contracts, Schemas, validators, and public-surface tests when the change affects compatibility

Identify the exact authorized work and verify the current `main`, branch Head, Lock reference commit, Runtime interface, and applicable CI before editing.

## Authority Hierarchy

The following hierarchy applies:

```text
Architect Runtime Authority Manifest and active Architect contracts
→ committed architect-authority.lock.json
→ Stage-QC process boundary and validation implementation
→ executable tests and exact-head workflow evidence
→ STATUS.md mutable project status
→ README.md orientation
→ PR prose and comments
```

`architect-authority.lock.json` is the sole executable consumer source for:

- Architect repository identity;
- reviewed reference commit;
- Runtime interface identity;
- public entry points;
- complete authority-file inventory;
- committed authority blob identities.

Do not derive executable authority from README text, PR descriptions, comments, duplicated lists, or remembered SHAs.

## Architect Ownership Boundary

Architect remains the sole owner of:

- Pipeline Stage inventory, order, versions, and successor rules;
- Runtime interface semantics and public operations;
- Stage Result and Run State derivation;
- Unknown lifecycle and candidate lock;
- canonical content fidelity rules;
- Payload issuance and provenance;
- Project Gate finalization and Handoff authority.

Stage-QC must not:

- copy or fork the Architect Runtime;
- create a second evaluator or state machine;
- import private Architect implementation surfaces as consumer contracts;
- accept caller-authored Stage Results, Run State, Payloads, provenance, eligibility, PASS claims, or Handoff Booleans as authority;
- reinterpret Architect Schemas or validation outcomes;
- silently broaden the Lock inventory or compatibility predicate.

## Fresh-Process Boundary

Every connection verification, prefinal validation, and final validation must:

1. start a fresh Python interpreter;
2. execute exactly one bounded operation;
3. import the official Architect public Runtime surface only inside the child;
4. return one bounded JSON-compatible result;
5. exit without persistent workers, process pools, fork inheritance, manual module cleanup, or prior-result reuse.

The Tkinter parent may wait in a background thread, but it must not import or execute the Architect Adapter, Runtime, or Core validation functions.

The process request must not carry Runtime objects, callables, Payloads, Stage Results, Run State, provenance, eligibility, artifacts, receipts, or Handoff authority.

## Compatibility and Lock Rules

Compatibility mode is `authority_file_identity`, not exact-commit or ancestry identity.

Before Runtime import, verification must fail closed unless all of the following hold:

- selected repository identity is correct;
- Runtime interface matches the committed Lock;
- the selected Architect Runtime Authority Manifest exactly matches the Lock inventory;
- every locked committed Git blob OID matches;
- every locked working-tree byte sequence equals the verified committed blob;
- every imported Runtime origin resolves to its exact expected path inside the selected checkout.

Hidden index flags, line-ending-only changes, missing files, directory replacement, unavailable blobs, stale Locks, and origin drift must not bypass verification.

Regenerate the Lock only when an Architect authority change has been deliberately reviewed. Lock regeneration must use an explicitly selected Architect checkout and the canonical Lock generator. Never hand-edit file identities.

## JSON and Evidence Identity

Preserve these distinct identities:

- raw Stage Output file SHA-256 for exact input bytes;
- Git blob OID for committed authority identity;
- exact working-tree byte equality for pre-import authority closure;
- canonical JSON SHA-256 for semantic JSON identity.

Do not substitute one identity mode for another.

Strict JSON must reject malformed UTF-8, BOMs, duplicate keys, non-finite numbers, non-object inputs, and malformed protocol results.

## Change Rules

For repository changes:

- use one focused branch from the current verified `main`;
- keep application, Lock, workflow, and documentation changes separated when their review boundaries differ;
- preserve public behavior unless a breaking change is explicitly authorized;
- update owning tests for every behavior or invariant change;
- update `STATUS.md` after meaningful implementation, compatibility, Lock, validation, workflow, or evidence-boundary changes;
- update `README.md` only for orientation or user workflow changes;
- avoid unrelated refactoring, formatting, dependency upgrades, and broad deletion;
- do not force-push, rewrite history, modify secrets, change repository permissions, or alter branch protection without explicit owner authorization;
- do not resolve review threads merely because code changed; leave final classification to fresh review when required;
- do not Merge, approve, deploy, release, or enable auto-merge unless explicitly authorized and supported by current evidence.

## Validation

Install the locked environment:

```text
uv sync --locked
```

Run basic compile and full tests:

```text
uv run python -m compileall -q src tests
uv run pytest -q
```

For exact Architect compatibility, set `PYTHONPATH=src`, select the exact Architect checkout, and run the canonical Lock check:

```text
python -m ev4_architect_stage_qc.lock_generator \
  --architect <path> \
  --lock architect-authority.lock.json \
  --expected-commit <sha> \
  --check
```

The canonical exact-pair validation workflow is:

```text
.github/workflows/validate.yml
```

It must verify the exact Stage-QC Head, Lock-selected Architect checkout, Lock/Manifest inventory, committed and working-tree authority identity, Runtime origins, compile, focused cross-repository tests, full tests, and whitespace.

Do not claim CI success, exact-head validation, cross-repository compatibility, or test counts without visible evidence bound to the exact Head.

Documentation-only changes do not require Runtime behavior changes or Lock regeneration. They still require diff review, link/path validation, and applicable repository workflows.

## Status and Reporting

Use explicit evidence states such as:

```text
observed
implemented
validated
inferred
proposed
unverified
insufficient_evidence
```

Keep repository names, branch names, file paths, commit SHAs, commands, Schema IDs, diagnostics, and status values in English.

`STATUS.md` must distinguish:

- the last functional merge from later documentation-only commits;
- exact-head CI from merge-tree identity;
- implementation evidence from deployment or production claims;
- actual selected Architect commit from Lock reference commit;
- current capability from future roadmap work.

## Evidence Boundaries

Never claim that Stage-QC evidence proves:

- live model-host enforcement;
- completion of a real non-synthetic Architect project;
- live Elementor execution;
- downstream Project Gate or CE acceptance;
- Builder or Responsive completion;
- deployment, release, or general production readiness;
- validity of future Heads without fresh validation.
