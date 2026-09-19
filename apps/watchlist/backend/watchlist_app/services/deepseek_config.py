"""DeepSeek model and endpoint shared by research and evidence acquisition."""

import os


def deepseek_model() -> str:
    return os.environ.get("INVESTMENT_STUDIO_PORTFOLIO_COPILOT_MODEL_NAME", "").strip() or "deepseek-v4.1-flash"


def deepseek_endpoint(*, search=False):
    # Match DSH: the configured namespace is used directly for chat completions.
    base = (os.environ.get("DEEPSEEK_BASE_URL", "").strip() or "https://gateway.hzxxf.cn/v1").rstrip("/")
    if search:
        return os.environ.get("DEEPSEEK_SEARCH_URL", "").strip() or base + "/messages"
    return base + "/chat/completions"
