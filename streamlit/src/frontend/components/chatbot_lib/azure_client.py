from __future__ import annotations

import importlib.util
from typing import Optional

from .constants import get_azure_endpoint

openai_spec = importlib.util.find_spec("openai")
if openai_spec is not None:  # pragma: no cover - imported at runtime when available
    from openai import APIStatusError, AzureOpenAI  # type: ignore[import]
else:  # pragma: no cover - dependency optional for linting
    APIStatusError = Exception  # type: ignore[misc]
    AzureOpenAI = None  # type: ignore[assignment]


def create_azure_client() -> Optional[AzureOpenAI]:
    endpoint, api_key, api_version = get_azure_endpoint()

    if not endpoint or not api_key or AzureOpenAI is None:
        return None

    return AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
