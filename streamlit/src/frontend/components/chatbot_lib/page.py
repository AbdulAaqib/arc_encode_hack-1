from __future__ import annotations

import os
from typing import Any, Dict, Iterable

import streamlit as st
from web3 import Web3

from ..config import (
    ARC_RPC_ENV,
    PRIVATE_KEY_ENV,
    GAS_LIMIT_ENV,
    GAS_PRICE_GWEI_ENV,
    SBT_ADDRESS_ENV,
    TRUSTMINT_SBT_ABI_PATH_ENV,
    LENDING_POOL_ADDRESS_ENV,
    LENDING_POOL_ABI_PATH_ENV,
    USDC_ADDRESS_ENV,
    USDC_ABI_PATH_ENV,
    USDC_DECIMALS_ENV,
)
from ..toolkit import build_llm_toolkit, build_lending_pool_toolkit, render_llm_history
from ..web3_utils import get_web3_client, load_contract_abi
from .attachments import build_attachment_context
from .azure_client import create_azure_client
from .chat_state import append_message, initialize_chat_state
from .constants import AZURE_DEPLOYMENT_ENV, WAVES_PATH
from .conversation import run_mcp_llm_conversation
from .lottie import load_lottie_json


def render_chatbot_page() -> None:
    """Render the chatbot page using Azure OpenAI chat completions with MCP tool support."""

    st.title("💬 PawChain Chatbot")
    st.caption(
        "Powered by OpenAI GPT-5 and MCP tools, using Streamlit's conversational components for a GPT-like experience."
    )

    attachments = st.file_uploader(
        "Attach documents (txt, md, pdf, docx, csv, json)",
        type=["txt", "md", "pdf", "docx", "csv", "json"],
        accept_multiple_files=True,
        key="chatbot_attachments",
    )
    include_attachments = st.checkbox(
        "Include attachments in next message",
        value=True,
        key="chatbot_include_attachments",
    )
    clip_len = int(os.getenv("CHATBOT_ATTACHMENT_MAX_CHARS", "6000"))

    client = create_azure_client()
    if client is None:
        st.info(
            "Set environment variables `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, and optionally `AZURE_OPENAI_API_VERSION` "
            "inside `.env` to enable the chatbot."
        )

    initialize_chat_state()

    render_llm_history(st.session_state.messages)

    prompt = st.chat_input(
        "Ask Doggo anything about setup, credit scoring, or MCP tooling…",
        key="chatbot_prompt",
    )
    if not prompt:
        return

    attachment_context = (
        build_attachment_context(attachments, clip_len) if (attachments and include_attachments) else ""
    )
    composed_prompt = (
        f"{prompt}\n\n[Attached documents]\n{attachment_context}" if attachment_context else prompt
    )

    append_message("user", composed_prompt)
    with st.chat_message("user"):
        st.markdown(prompt)
        if attachment_context:
            with st.expander("Attachments included in this turn"):
                for f in attachments:
                    st.write(f"- {getattr(f, 'name', 'document')}")

    if client is None:
        fallback = (
            "Azure OpenAI credentials are missing. Configure them to receive generated responses, or review the Intro page."
        )
        append_message("assistant", fallback)
        with st.chat_message("assistant"):
            st.markdown(fallback)
        return

    deployment = os.getenv(AZURE_DEPLOYMENT_ENV)
    if not deployment:
        warning = (
            "Environment variable `AZURE_OPENAI_CHAT_DEPLOYMENT` is not set. Add it to `.env` with your deployed "
            "Azure OpenAI model name."
        )
        append_message("assistant", warning)
        with st.chat_message("assistant"):
            st.warning(warning)
        return

    rpc_url = os.getenv(ARC_RPC_ENV)
    private_key = os.getenv(PRIVATE_KEY_ENV)
    default_gas_limit = int(os.getenv(GAS_LIMIT_ENV, "200000"))
    gas_price_gwei = os.getenv(GAS_PRICE_GWEI_ENV, "1")

    w3 = get_web3_client(rpc_url)
    if w3 is None:
        st.info("Connect to the RPC to unlock MCP tools in chat.")
        return

    sbt_address = os.getenv(SBT_ADDRESS_ENV)
    sbt_abi_path = os.getenv(TRUSTMINT_SBT_ABI_PATH_ENV)
    sbt_tools_schema: list[Dict[str, Any]] = []
    sbt_function_map: Dict[str, Any] = {}
    if sbt_address and sbt_abi_path:
        sbt_abi = load_contract_abi(sbt_abi_path)
        try:
            sbt_contract = w3.eth.contract(address=Web3.to_checksum_address(sbt_address), abi=sbt_abi)
            sbt_tools_schema, sbt_function_map = build_llm_toolkit(
                w3=w3,
                contract=sbt_contract,
                token_decimals=0,
                private_key=private_key,
                default_gas_limit=default_gas_limit,
                gas_price_gwei=gas_price_gwei,
            )
        except Exception:
            pass

    pool_address = os.getenv(LENDING_POOL_ADDRESS_ENV)
    pool_abi_path = os.getenv(LENDING_POOL_ABI_PATH_ENV)
    usdc_address = os.getenv(USDC_ADDRESS_ENV)
    usdc_abi_path = os.getenv(USDC_ABI_PATH_ENV)
    usdc_decimals = int(os.getenv(USDC_DECIMALS_ENV, "6"))
    pool_tools_schema: list[Dict[str, Any]] = []
    pool_function_map: Dict[str, Any] = {}
    if pool_address and pool_abi_path:
        pool_abi = load_contract_abi(pool_abi_path)
        usdc_abi = load_contract_abi(usdc_abi_path) if usdc_abi_path else None
        try:
            pool_contract = w3.eth.contract(address=Web3.to_checksum_address(pool_address), abi=pool_abi)
            pool_tools_schema, pool_function_map = build_lending_pool_toolkit(
                w3=w3,
                pool_contract=pool_contract,
                token_decimals=usdc_decimals,
                native_decimals=18,
                private_key=private_key,
                default_gas_limit=default_gas_limit,
                gas_price_gwei=gas_price_gwei,
            )
        except Exception:
            pass

    tools_schema = sbt_tools_schema + pool_tools_schema
    function_map = {**sbt_function_map, **pool_function_map}

    if not tools_schema:
        st.warning("No MCP tools are available for the current contract configuration.")
        return

    waves = load_lottie_json(WAVES_PATH)
    if waves:
        from streamlit_lottie import st_lottie_spinner

        with st.chat_message("assistant"):
            with st_lottie_spinner(waves, key="waves_spinner"):
                run_mcp_llm_conversation(
                    client, deployment, st.session_state.messages, tools_schema, function_map
                )
    else:
        with st.spinner("GPT 5 is orchestrating MCP tools…"):
            run_mcp_llm_conversation(
                client, deployment, st.session_state.messages, tools_schema, function_map
            )
