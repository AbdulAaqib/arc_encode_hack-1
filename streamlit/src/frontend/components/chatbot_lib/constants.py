from __future__ import annotations

import os
from pathlib import Path

COMPONENTS_DIR = Path(__file__).resolve().parents[1]
WAVES_PATH = COMPONENTS_DIR / "lottie_files" / "Waves.json"
AZURE_DEPLOYMENT_ENV = "AZURE_OPENAI_CHAT_DEPLOYMENT"

MCP_SYSTEM_PROMPT = (
    "You are PawChain's MCP automation copilot. Use the provided tools to check TrustMint SBT status and read/update "
    "credit scores on Arc. Prefer calling tools before responding, summarize results for the user, and suggest next steps."
)


def get_azure_endpoint() -> tuple[str | None, str | None, str | None]:
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_key = os.getenv("AZURE_OPENAI_KEY")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION")
    return endpoint, api_key, api_version
