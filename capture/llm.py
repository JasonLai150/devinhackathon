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
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from dataclasses import asdict, dataclass

_ANTHROPIC_DEFAULT = "claude-sonnet-4-20250514"
_OPENAI_DEFAULT = "gpt-4o-mini"


class ProviderError(RuntimeError):
    """A provider's HTTP error with safe, useful response details."""


def _safe_error_text(value, headers):
    text = str(value)
    secrets = [os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")]
    secrets.extend(v.removeprefix("Bearer ") for k, v in headers.items()
                   if k.lower() in ("x-api-key", "authorization"))
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return " ".join(text.split())[:1500]


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
    try:
        with urllib.request.urlopen(
                req, timeout=int(os.environ.get("LLM_HTTP_TIMEOUT", "120"))) as r:
            return json.load(r)
    except urllib.error.HTTPError as error:
        detail = {}
        try:
            body = json.loads(error.read(65536))
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                detail = body["error"]
        except (ValueError, UnicodeError):
            pass
        error_type = _safe_error_text(detail.get("type", "request_failed"), headers)
        message = _safe_error_text(detail.get("message", "Provider did not return a JSON error message."), headers)
        request_id = _safe_error_text(error.headers.get("request-id", "") if error.headers else "", headers)
        raise ProviderError(
            f"{urlsplit(url).hostname}: HTTP {error.code} ({error_type}): {message}"
            + (f" [request_id={request_id}]" if request_id else "")
        ) from None


def _anthropic(prompt, config, max_tokens=4096):
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
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    return "".join(b.get("text", "") for b in body["content"])


def _openai(prompt, config, max_tokens=None):
    # OPENAI_BASE_URL permits OpenAI-compatible providers (e.g. Gemini's
    # https://generativelanguage.googleapis.com/v1beta/openai endpoint).
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    body = _post(
        base.rstrip("/") + "/chat/completions",
        {
            "authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
            "content-type": "application/json",
        },
        {
            "model": config.model,
            "temperature": config.temperature,
            "messages": [{"role": "user", "content": prompt}],
            **({"max_tokens": max_tokens} if max_tokens is not None else {}),
        },
    )
    # Thinking models can exhaust max_tokens on reasoning and return no content.
    return body["choices"][0]["message"].get("content") or ""


def check_provider(config):
    """One tiny request validates the exact configured model before browser work."""
    call = _anthropic if config.provider == "anthropic" else _openai
    # Probe cheap first; thinking models can burn a small max_tokens budget
    # on reasoning and return empty content — retry once with headroom.
    for cap in (16, 1024):
        response = call("Reply with only OK.", config, max_tokens=cap)
        if isinstance(response, str) and response.strip():
            return {"ok": True, **config.metadata(), "max_output_tokens": cap}
    raise ProviderError("Provider returned no text during the model check.")
