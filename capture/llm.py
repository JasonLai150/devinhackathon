"""Optional LLM provider — urllib only, no new deps (same pattern as env/generate.py).

Returns a callable ``llm(prompt) -> str``. Picks Gemini, Anthropic or OpenAI from env
vars; raises RuntimeError when no key is present so callers can fall back to
scripted strategies.

Env:
    GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY
    GOOGLE_API_KEY                      (Gemini alias)
    CAPTURE_LLM_MODEL                    (override default model id)
"""

import json
import os
import re
import math
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit
from dataclasses import asdict, dataclass
from pathlib import Path

_ANTHROPIC_DEFAULT = "claude-sonnet-4-20250514"
_OPENAI_DEFAULT = "gpt-4o-mini"
_GEMINI_DEFAULT = "gemini-3.5-flash-lite"
_PROVIDERS = {
    "gemini": (("GEMINI_API_KEY", "GOOGLE_API_KEY"), _GEMINI_DEFAULT),
    "anthropic": (("ANTHROPIC_API_KEY",), _ANTHROPIC_DEFAULT),
    "openai": (("OPENAI_API_KEY",), _OPENAI_DEFAULT),
}
PROVIDERS = {provider: (keys[0], model) for provider, (keys, model) in _PROVIDERS.items()}


class ProviderError(RuntimeError):
    """A provider's HTTP error with safe, useful response details."""
    def __init__(self, message, status=None, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


_GEMINI_LOCK = threading.Lock()
_GEMINI_NEXT_REQUEST = 0.0


def _pace_gemini():
    """Share a request budget across browser workers, generation and distillation."""
    global _GEMINI_NEXT_REQUEST
    rpm = float(os.environ.get("GEMINI_RPM", "14"))
    if not math.isfinite(rpm) or rpm <= 0:
        raise ValueError("GEMINI_RPM must be a finite positive number")
    with _GEMINI_LOCK:
        delay = max(0, _GEMINI_NEXT_REQUEST - time.monotonic())
        if delay:
            time.sleep(delay)
        _GEMINI_NEXT_REQUEST = time.monotonic() + 60 / rpm


def _safe_error_text(value, headers):
    text = str(value)
    secrets = [os.environ.get(k) for keys, _ in _PROVIDERS.values() for k in keys]
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
        provider = next((p for p, (keys, _) in _PROVIDERS.items()
                         if any(os.environ.get(k) for k in keys)), "openai")
    if provider not in _PROVIDERS:
        raise ValueError("provider must be gemini, anthropic or openai")
    keys, default_model = _PROVIDERS[provider]
    if not any(os.environ.get(k) for k in keys):
        raise RuntimeError(f"{' / '.join(keys)} is not configured; configure it locally to run an LLM experiment")
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
    allowed = {key for keys, _ in _PROVIDERS.values() for key in keys} | {
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
    call = {"anthropic": _anthropic, "openai": _openai, "gemini": _gemini}[config.provider]
    return lambda prompt: call(prompt, config)


def default_llm():
    return make_llm(resolve_config())


def _post(url, headers, payload):
    gemini = urlsplit(url).hostname == "generativelanguage.googleapis.com"
    for attempt in range(3):
        if gemini:
            _pace_gemini()
        try:
            return _post_once(url, headers, payload)
        except ProviderError as error:
            if (not gemini or attempt == 2 or error.status not in (429, 503)
                    or error.retry_after is None or not 0 <= error.retry_after <= 90):
                raise
            time.sleep(error.retry_after + 0.5)


def _post_once(url, headers, payload):
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
        error_type = _safe_error_text(detail.get("type", detail.get("status", "request_failed")), headers)
        message = _safe_error_text(detail.get("message", "Provider did not return a JSON error message."), headers)
        request_id = _safe_error_text(error.headers.get("request-id", "") if error.headers else "", headers)
        retry_after = None
        for item in detail.get("details") or []:
            if isinstance(item, dict) and item.get("@type", "").endswith("google.rpc.RetryInfo"):
                try:
                    retry_after = float(str(item["retryDelay"]).removesuffix("s"))
                except (KeyError, TypeError, ValueError):
                    pass
        raise ProviderError(
            f"{urlsplit(url).hostname}: HTTP {error.code} ({error_type}): {message}"
            + (f" [request_id={request_id}]" if request_id else ""),
            status=error.code, retry_after=retry_after,
        ) from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise ProviderError(f"{urlsplit(url).hostname}: connection failed: "
                            + _safe_error_text(error, headers)) from None


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
    if max_tokens is None and config.temperature is not None:
        max_tokens = 8192
    model = quote(config.model.removeprefix("models/"), safe="")
    headers = {
        "x-goog-api-key": os.environ.get("GEMINI_API_KEY") or os.environ["GOOGLE_API_KEY"],
        "Content-Type": "application/json",
    }
    body = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers,
        {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
         "generationConfig": {
             **({"temperature": config.temperature} if config.temperature is not None else {}),
             **({"maxOutputTokens": max_tokens} if max_tokens is not None else {})}})
    candidates = body.get("candidates", [])
    if not candidates:
        reason = body.get("promptFeedback", {}).get("blockReason", "no candidates")
        raise ProviderError("Gemini returned no candidate: " + _safe_error_text(reason, headers))
    candidate = candidates[0]
    if candidate.get("finishReason") not in (None, "STOP"):
        raise ProviderError("Gemini response incomplete: " +
                            _safe_error_text(candidate["finishReason"], headers))
    text = "".join(part.get("text", "") for part in candidate.get("content", {}).get("parts", [])
                   if not part.get("thought"))
    if not text.strip():
        raise ProviderError("Gemini returned no answer text.")
    return text


def check_provider(config):
    """One tiny request validates the exact configured model before browser work."""
    calls = {"anthropic": _anthropic, "openai": _openai, "gemini": _gemini}
    # Probe cheap first; thinking models can burn a small max_tokens budget
    # on reasoning and return empty content — retry once with headroom.
    for cap in (256 if config.provider == "gemini" else 16, 1024):
        try:
            response = calls[config.provider]("Reply with only OK.", config, max_tokens=cap)
        except ProviderError as error:
            if cap != 1024 and config.provider == "gemini" and (
                    "MAX_TOKENS" in str(error) or "no answer text" in str(error)):
                continue
            raise
        if isinstance(response, str) and response.strip():
            return {"ok": True, **config.metadata(), "max_output_tokens": cap}
    raise ProviderError("Provider returned no text during the model check.")
