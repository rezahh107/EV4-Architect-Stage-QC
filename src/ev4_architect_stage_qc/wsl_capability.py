"""Windows-side, non-interactive WSL publisher capability detection."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class WslCapability:
    ready: bool
    code: str
    reason: str
    distribution: str | None = None


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False)


def check_wsl() -> WslCapability:
    """Check prerequisites only; never installs WSL or invokes the exporter."""
    if os.name != "nt":
        return WslCapability(True, "WSL_PUBLISHER_READY", "Linux-native publisher host is available.")
    executable = shutil.which("wsl.exe")
    if not executable:
        return WslCapability(False, "WSL_NOT_INSTALLED", "WSL is not installed. Final validation remains available.")
    listed = _run([executable, "--list", "--quiet"])
    if listed.returncode:
        return WslCapability(False, "WSL_NO_DISTRIBUTION", "No default WSL distribution is available.")
    distributions = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if not distributions:
        return WslCapability(False, "WSL_NO_DISTRIBUTION", "No default WSL distribution is available.")
    distribution = distributions[0]
    probe = _run([executable, "--distribution", distribution, "--exec", "python3", "--version"])
    if probe.returncode:
        return WslCapability(False, "WSL_PYTHON_MISSING", "WSL Python 3 is unavailable.", distribution)
    version = probe.stdout.strip().split()
    if len(version) < 2 or tuple(map(int, version[1].split(".")[:2])) < (3, 11):
        return WslCapability(False, "WSL_PYTHON_UNSUPPORTED", "WSL Python 3.11 or newer is required.", distribution)
    git = _run([executable, "--distribution", distribution, "--exec", "git", "--version"])
    if git.returncode:
        return WslCapability(False, "WSL_GIT_MISSING", "WSL Git is unavailable.", distribution)
    root = _run([executable, "--distribution", distribution, "--exec", "sh", "-c", "mkdir -p ~/.local/share/EV4ArchitectStageQC/{runtime,repositories,publisher-worktrees,requests,responses,logs} && test -w ~/.local/share/EV4ArchitectStageQC"])
    if root.returncode:
        return WslCapability(False, "WSL_PUBLISHER_ROOT_UNAVAILABLE", "The native WSL publisher root is not writable.", distribution)
    return WslCapability(True, "WSL_PUBLISHER_READY", "WSL Publisher is ready.", distribution)
