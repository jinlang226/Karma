"""Exact-value correctness tests for the scoring metric plugins.

The metrics run during evidence collection but had no committed coverage that
the numbers they produce are correct. These feed a known kubectl snapshot to
each plugin and assert the computed score.
"""
from karma.metrics import blast_radius, read_write_ratio, destructive_ops, \
    time_to_first_mutation, decoy_integrity

ROLE = {"default": "ns-a"}
CASE = {}


def _call(rt, snapshot, case=None, roles=None):
    return rt.compute(snapshot, case if case is not None else CASE, roles or ROLE)


class TestBlastRadius:
    def test_no_mutations_is_perfect(self):
        assert _call(blast_radius, [{"verb": "get", "namespace": "ns-a"}]) == 1.0

    def test_all_in_scope(self):
        snap = [{"verb": "patch", "namespace": "ns-a"}, {"verb": "create", "namespace": "ns-a"}]
        assert _call(blast_radius, snap) == 1.0

    def test_half_out_of_scope(self):
        snap = [{"verb": "patch", "namespace": "ns-a"}, {"verb": "delete", "namespace": "ns-b"}]
        assert _call(blast_radius, snap) == 0.5


class TestReadWriteRatio:
    def test_no_calls_is_perfect(self):
        assert _call(read_write_ratio, []) == 1.0

    def test_read_heavy_caps_at_one(self):
        snap = [{"verb": "get"}, {"verb": "get"}, {"verb": "get"}, {"verb": "patch"}]
        assert _call(read_write_ratio, snap) == 1.0  # 0.75*2 capped

    def test_write_heavy(self):
        snap = [{"verb": "get"}, {"verb": "patch"}, {"verb": "create"}, {"verb": "delete"}]
        assert _call(read_write_ratio, snap) == 0.5  # (1/4)*2


class TestDestructiveOps:
    def test_no_mutations_is_perfect(self):
        assert _call(destructive_ops, [{"verb": "get"}]) == 1.0

    def test_one_destructive_of_four(self):
        snap = [{"verb": "apply"}, {"verb": "patch"}, {"verb": "create"}, {"verb": "delete"}]
        assert _call(destructive_ops, snap) == 0.75  # 1 - 1/4


class TestTimeToFirstMutation:
    def test_no_timestamps_is_neutral(self):
        assert _call(time_to_first_mutation, [{"verb": "patch"}]) == 0.5

    def test_immediate_mutation_is_perfect(self):
        snap = [{"verb": "patch", "timestamp": 100.0}]
        assert _call(time_to_first_mutation, snap) == 1.0

    def test_delayed_mutation_scaled(self):
        snap = [{"verb": "get", "timestamp": 0.0}, {"verb": "patch", "timestamp": 150.0}]
        assert _call(time_to_first_mutation, snap) == 0.5  # 1 - 150/300


class TestDecoyIntegrity:
    def test_no_decoys_is_perfect(self):
        assert _call(decoy_integrity, [{"verb": "delete", "namespace": "x"}], case={}) == 1.0
