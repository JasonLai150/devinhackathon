"""Optional LLM provider — urllib only, no new deps (same pattern as env/generate.py).

Returns a callable ``llm(prompt) -> str``. Picks Gemini, Anthropic or OpenAI from env
vars; raises RuntimeError when no key is present so callers can fall back to
scripted strategies.

Env:
    GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY
    CAPTURE_LLM_MODEL                    (override default model id)
"""

import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit
from dataclasses import asdict, dataclass
from pathlib import Path

_ANTHROPIC_DEFAULT = "claude-sonnet-4-20250514"
_OPENAI_DEFAULT = "gpt-4o-mini"
_GEMINI_DEFAULT = "gemini-3.5-flash-lite"
PROVIDERS = {
    "gemini": ("GEMINI_API_KEY", _GEMINI_DEFAULT),
    "anthropic": ("ANTHROPIC_API_KEY", _ANTHROPIC_DEFAULT),
    "openai": ("OPENAI_API_KEY", _OPENAI_DEFAULT),
}


class ProviderError(RuntimeError):
    """A provider's HTTP error with safe, useful response details."""


def _safe_error_text(value, headers):
    text = str(value)
    secrets = [os.environ.get(k) for k in
               ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")]
    secrets.extend(v.removeprefix("Bearer ") for k, v in headers.items()
                   if k.lower() in ("x-api-key", "x-goog-api-key", "authorization"))
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return " ".join(text.split())[:1500]


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    temperature: float | None = 0.7

    def metadata(self):
        return asdict(self)


def resolve_config(provider=None, model=None, temperature=0.7):
    provider = provider or os.environ.get("CAPTURE_LLM_PROVIDER")
    if not provider:
        provider = next((p for p, (key, _) in PROVIDERS.items() if os.environ.get(key)), "openai")
    if provider not in PROVIDERS:
        raise ValueError("provider must be gemini, anthropic or openai")
    key, default_model = PROVIDERS[provider]
    if not os.environ.get(key):
        raise RuntimeError(f"{key} is not configured; configure it locally to run an LLM experiment")
    model = model or os.environ.get("CAPTURE_LLM_MODEL") or default_model
    # Gemini 3.6+ ignores sampling controls. Record the actual default behaviour.
    version = re.match(r"gemini-(\d+)(?:\.(\d+))?", model)
    if provider == "gemini" and version and (
            int(version[1]), int(version[2] or 0)) >= (3, 6):
        temperature = None
    if temperature is not None and not 0 <= temperature <= 1:
        raise ValueError("temperature must be between 0 and 1")
    if temperature is None and provider != "gemini":
        raise ValueError("this provider requires a temperature")
    return ModelConfig(provider, model, temperature)


def load_env_file(path):
    """Load local KEY=VALUE settings without logging values or running shell code."""
    values = {}
    allowed = {key for key, _ in PROVIDERS.values()} | {
        "CAPTURE_LLM_PROVIDER", "CAPTURE_LLM_MODEL", "ENV_LLM_MODEL"}
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or key not in allowed:
            raise ValueError(f"unsupported setting at env-file line {number}")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"unclosed quote at env-file line {number}")
            value = value[1:-1]
        if not value:
            raise ValueError(f"empty setting at env-file line {number}")
        values[key] = value
    for key, value in values.items():
        os.environ.setdefault(key, value)


def make_llm(config):
    """Freeze provider/model/sampling for all phases; credentials stay in the process."""
    providers = {"gemini": _gemini, "anthropic": _anthropic, "openai": _openai}
    return lambda prompt: providers[config.provider](prompt, config)


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


def _gemini(prompt, config, max_tokens=None):
    body = _post(
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{quote(config.model, safe='')}:generateContent",
        {"x-goog-api-key": os.environ["GEMINI_API_KEY"],
         "Content-Type": "application/json"},
        {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
         "generationConfig": {
             **({"temperature": config.temperature}
                if config.temperature is not None else {}),
             **({"maxOutputTokens": max_tokens} if max_tokens is not None else {})}})
    candidates = body.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no candidate response")
    candidate = candidates[0]
    if candidate.get("finishReason") != "STOP":
        raise RuntimeError(f"Gemini response incomplete: {candidate.get('finishReason', 'unknown')}")
    text = "".join(part.get("text", "") for part in candidate.get("content", {}).get("parts", [])
                   if not part.get("thought"))
    if not text.strip():
        raise RuntimeError("Gemini returned no answer text")
    return text


def check_provider(config):
    """One tiny request validates the exact configured model before browser work."""
    calls = {"anthropic": _anthropic, "openai": _openai, "gemini": _gemini}
    # Probe cheap first; thinking models can burn a small max_tokens budget
    # on reasoning and return empty content — retry once with headroom.
    for cap in (16, 1024):
        response = calls[config.provider]("Reply with only OK.", config, max_tokens=cap)
        if isinstance(response, str) and response.strip():
            return {"ok": True, **config.metadata(), "max_output_tokens": cap}
    raise ProviderError("Provider returned no text during the model check.")
