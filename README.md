# EV4 Architect Stage QC

A local, Windows-first Tkinter quality-control application. It is a GUI-only daily workflow: double-click `setup_windows.bat` once, then double-click `launch_windows.bat`.

## Daily workflow

1. Start the app. It detects a sibling `../EV4-Architect-Repo` checkout when available; otherwise use **Select Architect Repository**.
2. Use **Verify Architect Connection**. A compatible checkout is remembered in `%LOCALAPPDATA%\EV4ArchitectStageQC\settings.json` only after verification.
3. Select the folder containing the eleven prefinal Stage Output JSON files, then choose **Run Prefinal Validation**.
4. Open the resulting attempt folder and provide `generated-artifacts/architect-final-stage-context.json` to the model.
5. The model must produce a **Stage Output**, not a Stage Result or a PASS claim. Select that `/project-gate-export` JSON and run **Final Validation**.

Each run creates a new `results/attempt-####` folder beside the selected Stage folder. Inputs are copied before evaluation; source files are never changed. Failed attempts retain diagnostics.

## Fresh-process execution boundary

Every connection verification, prefinal validation, and final validation starts a new Python interpreter, performs exactly one operation, returns a bounded JSON-compatible result, and exits. The Tkinter parent may wait in a background thread, but it never imports or executes the Architect Adapter, Architect Runtime, or Core validation functions.

The parent request contains only the protocol identity, request identity, operation name, selected checkout path, bounded input paths, and `source_kind` where applicable. Payloads, Stage Results, Run State, provenance, eligibility, Runtime objects, callables, artifacts, receipts, and Handoff authority are not accepted through this boundary. Child startup, exit, transport, identity, schema, or operation failures are deterministic and fail closed; a result from a previous operation is never reused.

A successful child reports the exact origins of the official wrapper, `architect_quality_runtime` package, `architect_quality_runtime.history`, and `architect_project_gate_finalization`. Every origin must resolve to its exact expected path inside the verified selected checkout.

## Correctness model

The QC app loads the official evaluator from a local Architect checkout; it does not replace it. Compatibility is not an exact-commit or ancestry rule. The committed `architect-authority.lock.json` records the reviewed reference commit and the Git blob OID for every authority-bearing file. A selected checkout is accepted only when the repository identity, Runtime interface, every committed authority blob OID, and every actually imported working-tree byte sequence match that Lock. The actual checkout commit and the Lock reference commit remain separately visible in diagnostics and generated context.

The Lock is generated deterministically from an explicit Architect checkout and its committed Runtime Authority Manifest. The Manifest's complete Python and data authority inventories, plus the Manifest itself, must exactly equal the Lock file set. CI regenerates the canonical Lock in side-effect-free check mode before Runtime import. Documentation-only or other non-authority commits remain compatible when all locked authority blobs and working-tree bytes are unchanged.

PR descriptions may summarize exact-pair evidence, but they are non-authoritative. The committed Lock is the sole executable source of the selected Architect dependency identity and authority inventory.

Locked Runtime, Manifest, Schema, validator, and Project Gate contract files remain fail-closed: a committed or uncommitted change to any locked Authority file blocks validation until the Lock is deliberately regenerated and reviewed. Hidden index flags and line-ending-only changes do not bypass the raw byte comparison.

Strict JSON rejects malformed UTF-8, BOMs, duplicate keys, non-finite numbers, and non-object inputs. Raw-file SHA-256 identifies Stage Output input bytes. Authority identity uses committed Git blob OIDs plus exact working-tree byte equality. Canonical JSON SHA-256 identifies semantic JSON using sorted keys, compact separators, UTF-8, and `allow_nan=False`; these modes are intentionally distinct.

Prefinal artifacts are deterministic. Attempt IDs, timestamps, and paths are deliberately separated into `attempt-metadata.json`. A receipt is input-bound evidence only; Final Validation always replays all original prefinal Stage Outputs through the official evaluator.

## Common failures

- **Fresh child failure:** retry after checking the selected paths; no prior result is reused.
- **Module-origin mismatch:** select the intended Architect checkout and restore its exact locked files.
- **Stale Lock:** review the Architect authority change, then regenerate the canonical Lock from the explicitly selected checkout.
- **Changed authority file:** restore exact committed bytes or deliberately regenerate the Lock after review.
- **Missing/duplicate/wrong version Stage:** correct the selected Stage Output folder.
- **Official evaluation failure:** follow its affected-stage diagnostic; caller-generated PASS/digest/next-stage fields are not authoritative.
- **Terminal failure:** correct the model-produced terminal Stage Output or its upstream evidence, then replay.

Developer checks use `uv run pytest`. Canonical Lock commands are:

```text
python -m ev4_architect_stage_qc.lock_generator --architect <path> --lock architect-authority.lock.json --expected-commit <sha> --write
python -m ev4_architect_stage_qc.lock_generator --architect <path> --lock architect-authority.lock.json --expected-commit <sha> --check
```
