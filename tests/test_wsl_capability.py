from ev4_architect_stage_qc.wsl_capability import WslCapability


def test_capability_has_stable_fields():
    capability = WslCapability(False, "WSL_NOT_INSTALLED", "missing")
    assert capability.ready is False
    assert capability.code == "WSL_NOT_INSTALLED"
