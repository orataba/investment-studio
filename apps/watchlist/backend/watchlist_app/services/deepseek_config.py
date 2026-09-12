"""DeepSeek endpoint routing shared by research and evidence acquisition."""

import os


def deepseek_endpoint(*, search=False):
    # Match DSH: the configured namespace is used directly for chat completions.
    base = (os.environ.get("DEEPSEEK_BASE_URL", "").strip() or "https://api.deepseek.com").rstrip("/")
    if search:
        return os.environ.get("DEEPSEEK_SEARCH_URL", "").strip() or base.removesuffix("/v1") + "/anthropic/v1/messages"
    return base + "/chat/completions"
