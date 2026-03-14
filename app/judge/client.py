import json


class JudgeClientError(RuntimeError):
    pass


def _extract_text_content(payload):
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise JudgeClientError("missing choices in judge response")
    message = (choices[0] or {}).get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        text = "\n".join(part for part in parts if part)
        if text:
            return text
    raise JudgeClientError("missing message content in judge response")


class OpenAICompatibleJudgeClient:
    def __init__(
        self,
        base_url,
        api_key,
        model,
        timeout_sec=120,
        max_retries=2,
        referer=None,
        title=None,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout_sec = int(timeout_sec)
        self.max_retries = int(max_retries)
        self.referer = referer
        self.title = title
        self._client = None

    def create_judgement(self, messages):
        if not self.base_url:
            raise JudgeClientError("judge base URL is required")
        if not self.api_key:
            raise JudgeClientError("judge API key is required")
        if not self.model:
            raise JudgeClientError("judge model is required")

        last_error = None
        for _ in range(max(1, self.max_retries)):
            try:
                return self._request(messages)
            except Exception as exc:
                last_error = exc
        if isinstance(last_error, JudgeClientError):
            raise last_error
        raise JudgeClientError(str(last_error) if last_error else "judge request failed")

    def _request(self, messages):
        self._ensure_client()
        payload = {
            "model": self.model,
            "temperature": 0,
            "top_p": 1,
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        try:
            response = self._client.chat.completions.create(**payload)
        except Exception as exc:
            raise self._map_openai_error(exc) from exc

        parsed = self._to_json_dict(response)
        content = _extract_text_content(parsed)
        return {
            "raw_response": parsed,
            "content": content,
        }

    def _ensure_client(self):
        if self._client is not None:
            return
        try:
            from openai import OpenAI
        except Exception as exc:
            raise JudgeClientError(
                "openai SDK is required for judge client; install with `pip install openai`"
            ) from exc

        headers = {}
        if self.referer:
            headers["HTTP-Referer"] = self.referer
        if self.title:
            headers["X-Title"] = self.title
        kwargs = {
            "api_key": self.api_key,
            "base_url": self.base_url,
            "timeout": self.timeout_sec,
            "max_retries": 0,
        }
        if headers:
            kwargs["default_headers"] = headers
        self._client = OpenAI(**kwargs)

    @staticmethod
    def _to_json_dict(response):
        if response is None:
            raise JudgeClientError("judge returned empty response")
        if isinstance(response, dict):
            return response
        if hasattr(response, "model_dump"):
            try:
                return response.model_dump(mode="json")
            except Exception:
                pass
        if hasattr(response, "model_dump_json"):
            try:
                return json.loads(response.model_dump_json())
            except Exception:
                pass
        raise JudgeClientError("judge returned non-JSON payload")

    @staticmethod
    def _map_openai_error(exc):
        status_code = getattr(exc, "status_code", None)
        message = str(exc) or exc.__class__.__name__

        if status_code is not None:
            detail = None
            response = getattr(exc, "response", None)
            if response is not None:
                detail = getattr(response, "text", None)
                if callable(detail):
                    try:
                        detail = detail()
                    except Exception:
                        detail = None
            if detail and detail not in message:
                message = f"{message}: {detail}"
            return JudgeClientError(f"judge HTTP {status_code}: {message}")

        name = exc.__class__.__name__.lower()
        msg = message.lower()
        if "timeout" in name or "timed out" in msg:
            return JudgeClientError(f"judge request timeout: {message}")
        return JudgeClientError(f"judge request failed: {message}")
