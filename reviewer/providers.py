"""Makes the real model adapters from the keys in the environment. The only code that reads a provider key.

A provider with no key gets no adapter: its slots wait, marked "no adapter", and are filled by a later run. Nothing
here prints or stores a key.
"""
from typing import Dict

KEYS = {"anthropic": "ANTHROPIC_API_KEY", "google": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}


def real_adapters(env) -> Dict[str, object]:
    env = env or {}
    adapters: Dict[str, object] = {}
    if env.get(KEYS["anthropic"]):
        import anthropic  # needed only for a real run, so a dry run and the tests do not depend on it

        from .anthropic_adapter import ClaudeAdapter

        # The SDK's own retries are switched off: every try is made, counted and limited by this project's code.
        adapters["anthropic"] = ClaudeAdapter(anthropic.Anthropic(api_key=env[KEYS["anthropic"]], max_retries=0))
    if env.get(KEYS["google"]):
        from .google_adapter import GoogleAdapter

        adapters["google"] = GoogleAdapter(env[KEYS["google"]])
    if env.get(KEYS["openai"]):
        from .openai_adapter import OpenAIAdapter

        adapters["openai"] = OpenAIAdapter(env[KEYS["openai"]])
    return adapters
