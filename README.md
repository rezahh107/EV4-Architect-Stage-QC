# EV4 Architect Stage QC

A local, Windows-first Tkinter quality-control application. It is a GUI-only daily workflow: double-click `setup_windows.bat` once, then double-click `launch_windows.bat`.

## Daily workflow

1. Start the app. It detects a sibling `../EV4-Architect-Repo` checkout when available; otherwise use **Select Architect Repository**.
2. Use **Verify Architect Connection**. A compatible checkout is remembered in `%LOCALAPPDATA%\EV4ArchitectStageQC\settings.json` only after verification.
3. Select the folder containing the eleven prefinal Stage Output JSON files, then choose **Run Prefinal Validation**.
4. Open the resulting attempt folder and provide `generated-artifacts/architect-final-stage-context.json` to the model.
5. The model must produce a **Stage Output**, not a Stage Result or a PASS claim. Select that `/project-gate-export` JSON and run **Final Validation**.

Each run creates a new `results/attempt-####` folder beside the selected Stage folder. Inputs are copied before evaluation; source files are never changed. Failed attempts retain diagnostics.

## Correctness model

The QC app loads the official evaluator from a local Architect checkout; it does not replace it. It is not tied to one permanent Architect commit: new Architect commits remain compatible when every locked Authority file has the same raw SHA-256 identity recorded in `architect-authority.lock.json`. The observed checkout commit remains in provenance and diagnostics.

Locked Runtime, Manifest, Schema, validator, and Project Gate contract files remain fail-closed: a committed or uncommitted change to any locked Authority file blocks validation until the QC lock is deliberately reviewed and updated. Normal documentation, content, and unrelated repository changes do not require a QC update.

Strict JSON rejects malformed UTF-8, BOMs, duplicate keys, non-finite numbers, and non-object inputs. Raw-file SHA-256 identifies exact input/authority bytes. Canonical JSON SHA-256 identifies semantic JSON using sorted keys, compact separators, UTF-8, and `allow_nan=False`; these modes are intentionally distinct.

Prefinal artifacts are deterministic. Attempt IDs, timestamps, and paths are deliberately separated into `attempt-metadata.json`. A receipt is input-bound evidence only; Final Validation always replays all original prefinal Stage Outputs through the official evaluator.

## Common failures

- **Changed authority file:** review the Architect authority change, then update this QC application with a deliberately reviewed compatible lock.
- **Missing/duplicate/wrong version Stage:** correct the selected Stage Output folder.
- **Official evaluation failure:** follow its affected-stage diagnostic; caller-generated PASS/digest/next-stage fields are not authoritative.
- **Terminal failure:** correct the model-produced terminal Stage Output or its upstream evidence, then replay.

Developer checks use `uv run pytest` and are not required for normal user operation.
