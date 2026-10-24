"""Optional LLM provider — urllib only, no new deps (same pattern as env/generate.py).

Returns a callable ``llm(prompt) -> str``. Picks Anthropic or OpenAI from env
vars; raises RuntimeError when no key is present so callers can fall back to
scripted strategies.

Env:
    ANTHROPIC_API_KEY / OPENAI_API_KEY   (provider selection, in that order)
    CAPTURE_LLM_MODEL                    (override default model id)
"""

import json
import os
import urllib.request
from dataclasses import asdict, dataclass

_ANTHROPIC_DEFAULT = "claude-sonnet-4-20250514"
_OPENAI_DEFAULT = "gpt-4o-mini"


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    temperature: float = 0.7

    def metadata(self):
        return asdict(self)


def resolve_config(provider=None, model=None, temperature=0.7):
    provider = provider or os.environ.get("CAPTURE_LLM_PROVIDER")
    if not provider:
        provider = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
    if provider not in ("anthropic", "openai"):
        raise ValueError("provider must be anthropic or openai")
    key = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    if not os.environ.get(key):
        raise RuntimeError(f"{key} is not configured; configure it locally to run an LLM experiment")
    if not 0 <= temperature <= 1:
        raise ValueError("temperature must be between 0 and 1")
    return ModelConfig(provider, model or os.environ.get("CAPTURE_LLM_MODEL") or
                       (_ANTHROPIC_DEFAULT if provider == "anthropic" else _OPENAI_DEFAULT),
                       temperature)


def make_llm(config):
    """Freeze provider/model/sampling for all phases; credentials stay in the process."""
    return lambda prompt: (_anthropic(prompt, config) if config.provider == "anthropic"
                           else _openai(prompt, config))


def default_llm():
    return make_llm(resolve_config())


def _post(url, headers, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _anthropic(prompt, config):
    body = _post(
        "https://api.anthropic.com/v1/messages",
        {
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        {
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    return "".join(b.get("text", "") for b in body["content"])


def _openai(prompt, config):
    body = _post(
        "https://api.openai.com/v1/chat/completions",
        {
            "authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
            "content-type": "application/json",
        },
        {
            "model": config.model,
            "temperature": config.temperature,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    return body["choices"][0]["message"]["content"]
