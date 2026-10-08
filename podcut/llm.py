"""Provider-agnostic LLM client (Groq / xAI Grok / OpenAI all speak the OpenAI chat API)."""
from __future__ import annotations

import json
import re
import time

from .config import get_api_key

PROVIDERS = {
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile"},
    "xai": {"base_url": "https://api.x.ai/v1", "model": "grok-3-mini"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
}

# Groq retires models from time to time: the first of these that the key can use wins
GROQ_FALLBACKS = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "meta-llama/llama-4-maverick-17b-128e-instruct",
                  "moonshotai/kimi-k2-instruct", "qwen/qwen3-32b", "meta-llama/llama-4-scout-17b-16e-instruct",
                  "openai/gpt-oss-20b", "llama-3.1-8b-instant"]


def pick_groq_model(client, wanted: str, log=print) -> str:
    try:
        available = {m.id for m in client.models.list().data}
    except Exception as exc:
        log(f"Could not list Groq models ({exc}); using {wanted}")
        return wanted
    for m in [wanted] + GROQ_FALLBACKS:
        if m in available:
            return m
    chat = sorted(m for m in available if not any(x in m for x in ("whisper", "tts", "guard", "embed")))
    return chat[0] if chat else wanted


def detect_provider(key: str) -> str:
    if key.startswith("gsk_"):
        return "groq"
    if key.startswith("xai-"):
        return "xai"
    return "openai"


class LLMUnavailable(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: dict, log=print):
        from openai import OpenAI

        key = get_api_key()
        if not key:
            raise LLMUnavailable("No API key found (set GROQ_API_KEY / XAI_API_KEY / LLM_API_KEY in .env)")
        provider = cfg.get("provider") or "auto"
        if provider == "auto":
            provider = detect_provider(key)
        if provider not in PROVIDERS:
            raise LLMUnavailable(f"Unknown LLM provider '{provider}'")
        self.provider = provider
        self.model = cfg.get("model") or PROVIDERS[provider]["model"]
        self.temperature = float(cfg.get("temperature", 0.2))
        self.retries = int(cfg.get("max_retries", 4))
        self.log = log
        # the SDK already retries 429/5xx and honours Retry-After headers
        self.client = OpenAI(api_key=key, base_url=PROVIDERS[provider]["base_url"],
                             max_retries=self.retries, timeout=90)
        if provider == "groq":
            self.model = pick_groq_model(self.client, self.model, log)
        log(f"LLM: {self.model} ({provider})")

    def _complete(self, system: str, user: str):
        import openai

        kwargs = dict(model=self.model, temperature=self.temperature,
                      messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
        if "gpt-oss" in self.model:
            kwargs["extra_body"] = {"reasoning_effort": "low"}
        try:
            return self.client.chat.completions.create(response_format={"type": "json_object"}, **kwargs)
        except openai.BadRequestError:  # model without JSON mode / reasoning options
            kwargs.pop("extra_body", None)
            return self.client.chat.completions.create(**kwargs)

    def json_chat(self, system: str, user: str) -> dict:
        last_err: Exception | None = None
        for attempt in range(1, 3):
            try:
                resp = self._complete(system, user)
                content = resp.choices[0].message.content or ""
                return parse_json(content)
            except ValueError as exc:  # bad JSON -> ask again
                last_err = exc
                self.log(f"LLM returned invalid JSON (attempt {attempt}): {exc}")
                time.sleep(2)
        raise RuntimeError(f"LLM kept returning invalid JSON: {last_err}")


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not m:
            raise ValueError("no JSON object in response")
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            raise ValueError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("response JSON is not an object")
    return data
