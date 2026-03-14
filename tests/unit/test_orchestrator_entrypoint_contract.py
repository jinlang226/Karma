import orchestrator
from app.orchestrator_core import runtime_glue


def test_orchestrator_public_entrypoint_contract():
    assert callable(orchestrator.main)
    exported = list(orchestrator.__all__)
    assert exported
    assert exported[0] == "main"
    assert "main" in exported
    assert len(exported) == len(set(exported))


def test_orchestrator_compat_surface_is_explicit_and_resolvable():
    compat = orchestrator._COMPAT_EXPORTS
    assert isinstance(compat, tuple)
    assert len(compat) == len(set(compat))
    assert all(isinstance(name, str) and name for name in compat)
    assert compat == ()

    # Guard against accidental wildcard exports by requiring __all__ to be
    # exactly entrypoint + explicit compatibility allowlist.
    assert orchestrator.__all__ == ["main", *compat]
    assert orchestrator.__all__ == ["main"]

    for name in compat:
        assert name in orchestrator.__all__
        assert hasattr(orchestrator, name)
        assert hasattr(runtime_glue, name)
        assert getattr(orchestrator, name) is getattr(runtime_glue, name)
