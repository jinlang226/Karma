from app.judge.client import JudgeClientError, OpenAICompatibleJudgeClient


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self, mode="json"):
        return dict(self._payload)


class _FakeCompletions:
    def __init__(self, fn):
        self._fn = fn

    def create(self, **kwargs):
        return self._fn(**kwargs)


class _FakeChat:
    def __init__(self, fn):
        self.completions = _FakeCompletions(fn)


class _FakeOpenAIClient:
    def __init__(self, fn):
        self.chat = _FakeChat(fn)


def test_client_requires_base_url_api_key_and_model():
    client = OpenAICompatibleJudgeClient(base_url="", api_key="", model="")
    try:
        client.create_judgement([])
        raise AssertionError("expected JudgeClientError")
    except JudgeClientError as exc:
        assert "base URL is required" in str(exc)


def test_client_success_parses_response_content():
    payload = {
        "choices": [
            {
                "message": {
                    "content": '{"dimension_scores":[],"milestone_coverage":{"covered":[],"missed":[]},"anti_pattern_flags":[],"overall_assessment":"ok","limitations":[]}'
                }
            }
        ]
    }
    client = OpenAICompatibleJudgeClient(
        base_url="https://openrouter.ai/api/v1",
        api_key="k",
        model="openai/gpt-oss-120b:free",
    )
    client._client = _FakeOpenAIClient(lambda **kwargs: _FakeResponse(payload))
    result = client.create_judgement([{"role": "user", "content": "hi"}])
    assert "raw_response" in result
    assert "content" in result
    assert "dimension_scores" in result["content"]


def test_client_maps_status_code_errors():
    class _Resp:
        text = '{"error":"rate_limit"}'

    class _Err(Exception):
        def __init__(self):
            super().__init__("rate limited")
            self.status_code = 429
            self.response = _Resp()

    client = OpenAICompatibleJudgeClient(
        base_url="https://openrouter.ai/api/v1",
        api_key="k",
        model="openai/gpt-oss-120b:free",
        max_retries=1,
    )
    client._client = _FakeOpenAIClient(lambda **kwargs: (_ for _ in ()).throw(_Err()))
    try:
        client.create_judgement([{"role": "user", "content": "hi"}])
        raise AssertionError("expected JudgeClientError")
    except JudgeClientError as exc:
        text = str(exc)
        assert "judge HTTP 429" in text
        assert "rate limited" in text


def test_client_retries_then_succeeds():
    payload = {
        "choices": [
            {
                "message": {
                    "content": '{"dimension_scores":[],"milestone_coverage":{"covered":[],"missed":[]},"anti_pattern_flags":[],"overall_assessment":"ok","limitations":[]}'
                }
            }
        ]
    }
    state = {"calls": 0}

    def _call(**kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("temporary transport failure")
        return _FakeResponse(payload)

    client = OpenAICompatibleJudgeClient(
        base_url="https://openrouter.ai/api/v1",
        api_key="k",
        model="openai/gpt-oss-120b:free",
        max_retries=2,
    )
    client._client = _FakeOpenAIClient(_call)
    result = client.create_judgement([{"role": "user", "content": "hi"}])
    assert state["calls"] == 2
    assert "content" in result
