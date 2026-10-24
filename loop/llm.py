"""LLM provider for distillation — delegates to B's wiring.

env.generate.default_llm picks Gemini, Anthropic or OpenAI from env keys, model
via ENV_LLM_MODEL, and returns None when no key is set (the distill
heuristic fallback covers that case). Kept as a module seam so tests and
callers can inject their own callable.
"""
from env.generate import default_llm  # noqa: F401  (re-exported seam)
