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

_ANTHROPIC_DEFAULT = "claude-sonnet-4-20250514"
_OPENAI_DEFAULT = "gpt-4o-mini"


def default_llm():
    """Return ``llm(prompt) -> str`` or raise RuntimeError if no key is set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return _anthropic
    if os.environ.get("OPENAI_API_KEY"):
        return _openai
    raise RuntimeError(
        "no LLM key set (ANTHROPIC_API_KEY / OPENAI_API_KEY) — use scripted strategies"
    )


def _post(url, headers, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _anthropic(prompt):
    body = _post(
        "https://api.anthropic.com/v1/messages",
        {
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        {
            "model": os.environ.get("CAPTURE_LLM_MODEL", _ANTHROPIC_DEFAULT),
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    return "".join(b.get("text", "") for b in body["content"])


def _openai(prompt):
    body = _post(
        "https://api.openai.com/v1/chat/completions",
        {
            "authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
            "content-type": "application/json",
        },
        {
            "model": os.environ.get("CAPTURE_LLM_MODEL", _OPENAI_DEFAULT),
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    return body["choices"][0]["message"]["content"]
