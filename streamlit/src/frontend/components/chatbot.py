"""Chatbot page component backed by Azure OpenAI chat completions."""

from __future__ import annotations

import importlib.util
import os
from typing import Iterable, Optional

import streamlit as st

openai_spec = importlib.util.find_spec("openai")
if openai_spec is not None:  # pragma: no cover - imported at runtime when available
    from openai import APIStatusError, AzureOpenAI  # type: ignore[import]
else:  # pragma: no cover - dependency optional for linting
    APIStatusError = Exception  # type: ignore[misc]
    AzureOpenAI = None  # type: ignore[assignment]


AZURE_DEPLOYMENT_ENV = "AZURE_OPENAI_CHAT_DEPLOYMENT"


@st.cache_resource(show_spinner=False)
def _create_azure_client() -> Optional[AzureOpenAI]:
    """Instantiate a cached Azure OpenAI client from environment variables."""

    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    api_key = os.getenv("AZURE_OPENAI_KEY")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION")

    if not endpoint or not api_key or AzureOpenAI is None:
        return None

    return AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)


def _initialize_chat_state() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = [
            {
                "role": "assistant",
                "content": "Hi! I'm Doggo, your PawChain guide. Ask me anything about Arc credit flows or this dashboard.",
            }
        ]


def render_chatbot_page() -> None:
    """Render the chatbot page using Azure OpenAI chat completions, mirroring Streamlit's tutorial flow."""

    st.title("💬 PawChain Chatbot")
    st.caption(
        "Powered by OpenAI GPT-5 and MCP tools, using Streamlit's conversational components for a GPT-like experience."
    )

    client = _create_azure_client()
    if client is None:
        st.info(
            "Set environment variables `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, and optionally `AZURE_OPENAI_API_VERSION` "
            "inside `.env` to enable the chatbot."
        )

    _initialize_chat_state()

    # Always ensure the MCP system prompt is first so the model knows it has tools
    msgs = st.session_state.messages
    if not msgs or msgs[0].get("role") != "system":
        msgs.insert(0, {"role": "system", "content": _MCP_SYSTEM_PROMPT})

    # Render history that supports tool messages
    render_llm_history(st.session_state.messages)

    # Single chat input
    prompt = st.chat_input(
        "Ask Doggo anything about setup, credit scoring, or MCP tooling…",
        key="chatbot_prompt",
    )
    if not prompt:
        return

    _append_message("user", prompt)
    with st.chat_message("user"):
        st.markdown(prompt)

    # Azure OpenAI pre-flight
    if client is None:
        fallback = (
            "Azure OpenAI credentials are missing. Configure them to receive generated responses, or review the Intro page."
        )
        _append_message("assistant", fallback)
        with st.chat_message("assistant"):
            st.markdown(fallback)
        return

    deployment = os.getenv(AZURE_DEPLOYMENT_ENV)
    if not deployment:
        warning = (
            "Environment variable `AZURE_OPENAI_CHAT_DEPLOYMENT` is not set. Add it to `.env` with your deployed "
            "Azure OpenAI model name."
        )
        _append_message("assistant", warning)
        with st.chat_message("assistant"):
            st.warning(warning)
        return

    # Build Web3 + contract for MCP tools (single chat experience, tool-enabled)
    rpc_url = os.getenv(ARC_RPC_ENV)
    contract_address = os.getenv("CREDIT_SCORE_REGISTRY_ADDRESS")
    abi_path = os.getenv("ARC_CREDIT_LINE_MANAGER_ABI_PATH")
    private_key = os.getenv(PRIVATE_KEY_ENV)
    token_decimals = int(os.getenv(USDC_DECIMALS_ENV, "6"))
    default_gas_limit = int(os.getenv(GAS_LIMIT_ENV, "200000"))
    gas_price_gwei = os.getenv(GAS_PRICE_GWEI_ENV, "1")

    w3 = get_web3_client(rpc_url)
    abi = load_contract_abi(abi_path)

    if w3 is None:
        st.info("Connect to the RPC and provide contract details to unlock MCP tools in chat.")
        return
    if not abi or not contract_address:
        st.info(
            "Set `CREDIT_SCORE_REGISTRY_ADDRESS` and `ARC_CREDIT_LINE_MANAGER_ABI_PATH` in `.env` to unlock MCP tools."
        )
        return

    try:
        contract = w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=abi)
    except Exception as exc:
        st.error(f"Unable to build contract instance: {exc}")
        return

    tools_schema, function_map = build_llm_toolkit(
        w3=w3,
        contract=contract,
        token_decimals=token_decimals,
        private_key=private_key,
        default_gas_limit=default_gas_limit,
        gas_price_gwei=gas_price_gwei,
    )

    if not tools_schema:
        st.warning("No MCP tools are available for the current contract configuration.")
        return

    # Tool-enabled chat loop (single chat)
    with st.spinner("Azure OpenAI is orchestrating MCP tools…"):
        _run_mcp_llm_conversation(
            client, deployment, st.session_state.messages, tools_schema, function_map
        )

    return


def _stream_chunks(stream: Iterable) -> Iterable[str]:
    """Yield token deltas from the streaming Azure OpenAI response."""

    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


def _append_message(role: str, content: str) -> None:
    st.session_state.messages.append({"role": role, "content": content})


# === MCP LLM Playground (moved from Tools page) ===
import json
from typing import Any, Dict
from web3 import Web3

# Prefer config, but fall back to string literals if import fails
try:
    from .config import (
        ARC_RPC_ENV,
        PRIVATE_KEY_ENV,
        USDC_DECIMALS_ENV,
        GAS_LIMIT_ENV,
        GAS_PRICE_GWEI_ENV,
    )
except Exception:  # pragma: no cover - fallback values allow the UI to load
    ARC_RPC_ENV = "ARC_TESTNET_RPC_URL"
    PRIVATE_KEY_ENV = "PRIVATE_KEY"
    USDC_DECIMALS_ENV = "ARC_USDC_DECIMALS"
    GAS_LIMIT_ENV = "ARC_GAS_LIMIT"
    GAS_PRICE_GWEI_ENV = "ARC_GAS_PRICE_GWEI"

from .web3_utils import get_web3_client, load_contract_abi
from .toolkit import (
    build_llm_toolkit,
    render_llm_history,
    render_tool_message,
    tool_error,
    tool_success,
)

_MCP_SYSTEM_PROMPT = (
    "You are PawChain's MCP automation copilot. Use the provided tools to inspect wallet balances, credit availability, and "
    "credit score data on Arc. Prefer calling tools before responding, summarize results in business-friendly language, and "
    "suggest next steps for the borrower when appropriate."
)


def _run_mcp_llm_conversation(
    client: Any,
    deployment: str,
    messages: list[Dict[str, Any]],
    tools_schema: list[Dict[str, Any]],
    function_map: Dict[str, Any],
) -> None:
    pending = client.chat.completions.create(
        model=deployment,
        messages=messages,
        tools=tools_schema,
        tool_choice="auto",
    )

    while True:
        message = pending.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []

        if tool_calls:
            messages.append(message.model_dump())
            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                args_payload = tool_call.function.arguments or "{}"
                try:
                    arguments = json.loads(args_payload) if args_payload else {}
                except json.JSONDecodeError:
                    arguments = {}

                handler = function_map.get(tool_name)
                if handler is None:
                    tool_output = tool_error(f"Tool '{tool_name}' is not registered.")
                else:
                    try:
                        response_payload = handler(**arguments)
                        tool_output = response_payload if isinstance(response_payload, str) else tool_success(response_payload)
                    except Exception as exc:  # pragma: no cover - surfaced via UI only
                        tool_output = tool_error(str(exc))

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_name,
                        "content": tool_output,
                    }
                )
                render_tool_message(tool_name, tool_output)

            pending = client.chat.completions.create(
                model=deployment,
                messages=messages,
                tools=tools_schema,
                tool_choice="auto",
            )
            continue

        content = getattr(message, "content", None)
        if content:
            messages.append({"role": "assistant", "content": content})
            with st.chat_message("assistant"):
                st.markdown(content)
        break


def render_mcp_llm_playground_section() -> None:
    st.subheader("MCP LLM Playground")

    # Pre-flight Azure
    client = _create_azure_client()
    if client is None:
        st.info(
            "Configure Azure OpenAI credentials in `.env` (`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, "
            "`AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_CHAT_DEPLOYMENT`) to use the MCP assistant."
        )
        return

    deployment = os.getenv(AZURE_DEPLOYMENT_ENV)
    if not deployment:
        st.warning("Set `AZURE_OPENAI_CHAT_DEPLOYMENT` in `.env` to enable Azure OpenAI chat completions.")
        return

    # Build Web3 + contract
    rpc_url = os.getenv(ARC_RPC_ENV)
    contract_address = os.getenv("CREDIT_SCORE_REGISTRY_ADDRESS")
    abi_path = os.getenv("ARC_CREDIT_LINE_MANAGER_ABI_PATH")
    private_key = os.getenv(PRIVATE_KEY_ENV)
    token_decimals = int(os.getenv(USDC_DECIMALS_ENV, "6"))
    default_gas_limit = int(os.getenv(GAS_LIMIT_ENV, "200000"))
    gas_price_gwei = os.getenv(GAS_PRICE_GWEI_ENV, "1")

    w3 = get_web3_client(rpc_url)
    abi = load_contract_abi(abi_path)

    if w3 is None:
        st.info("Connect to the RPC and provide contract details to unlock the MCP playground.")
        return
    if not abi or not contract_address:
        st.info("Set `CREDIT_SCORE_REGISTRY_ADDRESS` and `ARC_CREDIT_LINE_MANAGER_ABI_PATH` in `.env` to unlock the MCP playground.")
        return

    try:
        contract = w3.eth.contract(address=Web3.to_checksum_address(contract_address), abi=abi)
    except Exception as exc:
        st.error(f"Unable to build contract instance: {exc}")
        return

    tools_schema, function_map = build_llm_toolkit(
        w3=w3,
        contract=contract,
        token_decimals=token_decimals,
        private_key=private_key,
        default_gas_limit=default_gas_limit,
        gas_price_gwei=gas_price_gwei,
    )

    if not tools_schema:
        st.warning("No MCP tools are available for the current contract configuration.")
        return

    messages = st.session_state.setdefault(
        "mcp_llm_messages",
        [{"role": "system", "content": _MCP_SYSTEM_PROMPT}],
    )

    render_llm_history(messages)

    prompt = st.chat_input(
        "Ask the MCP assistant to inspect wallets, credit limits, or contract data…",
        key="mcp_llm_prompt",
    )
    if not prompt:
        return

    with st.chat_message("user"):
        st.markdown(prompt)
    messages.append({"role": "user", "content": prompt})

    with st.spinner("Azure OpenAI is orchestrating MCP tools…"):
        _run_mcp_llm_conversation(client, deployment, messages, tools_schema, function_map)

