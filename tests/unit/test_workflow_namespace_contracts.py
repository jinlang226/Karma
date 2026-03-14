import re

from app.workflow import build_alias_namespace_map, resolve_stage_namespace_context


_DNS_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def test_build_alias_namespace_map_is_dns_safe_unique_and_bounded():
    aliases = ["cluster_alpha", "cluster_beta", "cluster_gamma"]
    run_token = "RUN_TOKEN_WITH_UPPERCASE_AND_SYMBOLS___" + ("x" * 120)
    prefix = "WORKFLOW_PREFIX_" + ("y" * 40)

    out_a = build_alias_namespace_map(aliases, run_token=run_token, prefix=prefix)
    out_b = build_alias_namespace_map(aliases, run_token=run_token, prefix=prefix)

    assert out_a == out_b
    assert set(out_a.keys()) == set(aliases)
    values = list(out_a.values())
    assert len(values) == len(set(values))
    for value in values:
        assert len(value) <= 63
        assert _DNS_LABEL.match(value)


def test_resolve_stage_namespace_context_raises_on_unresolved_alias():
    stage = {
        "id": "stage_1",
        "namespaces": ["cluster_a", "cluster_b"],
        "namespace_binding": {"source": "cluster_a", "target": "cluster_b"},
    }

    failed = False
    try:
        resolve_stage_namespace_context(stage, {"cluster_a": "cmp-a"})
    except ValueError as exc:
        failed = True
        assert "namespace alias is not resolved" in str(exc)
        assert "cluster_b" in str(exc)
    assert failed is True
