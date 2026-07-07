"""Unit tests for karma.judge.run_score._parse_adjudication (M4).

The regression-sweep adjudicator must be conservative: any answer it cannot read
as an explicit verdict keeps the regression as legitimate (penalty preserved),
so a malformed or injected response can never forgive a regression into a 100.
"""

from karma.judge.run_score import _parse_adjudication


class TestParseAdjudication:
    def test_missing_key_defaults_to_legitimate_regression(self):
        # A dict WITHOUT the verdict key must keep the penalty (was the M4 bug:
        # bool(None) -> False forgave the regression).
        r = _parse_adjudication('{"reasoning": "no verdict field"}')
        assert r["legitimate_regression"] is True

    def test_non_dict_defaults_to_legitimate_regression(self):
        assert _parse_adjudication("not json").get("legitimate_regression") is True

    def test_explicit_false_is_respected(self):
        assert _parse_adjudication('{"legitimate_regression": false}')["legitimate_regression"] is False

    def test_explicit_true_is_respected(self):
        assert _parse_adjudication('{"legitimate_regression": true}')["legitimate_regression"] is True

    def test_string_verdicts_parse(self):
        assert _parse_adjudication('{"legitimate_regression": "false"}')["legitimate_regression"] is False
        assert _parse_adjudication('{"legitimate_regression": "yes"}')["legitimate_regression"] is True
