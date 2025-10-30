from __future__ import annotations

import os
from typing import Any, Dict

import streamlit as st
from web3 import Web3

from ..config import (
    ARC_RPC_ENV,
    PRIVATE_KEY_ENV,
    GAS_LIMIT_ENV,
    GAS_PRICE_GWEI_ENV,
    SBT_ADDRESS_ENV,
    TRUSTMINT_SBT_ABI_PATH_ENV,
)
from ..toolkit import build_llm_toolkit, render_llm_history
from ..web3_utils import get_web3_client, load_contract_abi
from .azure_client import create_azure_client
from .constants import AZURE_DEPLOYMENT_ENV, MCP_SYSTEM_PROMPT, WAVES_PATH
from .conversation import run_mcp_llm_conversation
from .lottie import load_lottie_json


def render_mcp_llm_playground_section() -> None:
    st.subheader("MCP LLM Playground")

    client = create_azure_client()
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

    rpc_url = os.getenv(ARC_RPC_ENV)
    contract_address = os.getenv(SBT_ADDRESS_ENV)
    abi_path = os.getenv(TRUSTMINT_SBT_ABI_PATH_ENV)
    private_key = os.getenv(PRIVATE_KEY_ENV)
    token_decimals = 0
    default_gas_limit = int(os.getenv(GAS_LIMIT_ENV, "200000"))
    gas_price_gwei = os.getenv(GAS_PRICE_GWEI_ENV, "1")

    w3 = get_web3_client(rpc_url)
    abi = load_contract_abi(abi_path)

    if w3 is None:
        st.info("Connect to the RPC and provide TrustMintSBT details to unlock the MCP playground.")
        return
    if not abi or not contract_address:
        st.info("Set `SBT_ADDRESS` and `TRUSTMINT_SBT_ABI_PATH` in `.env` to unlock the MCP playground.")
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
        [{"role": "system", "content": MCP_SYSTEM_PROMPT}],
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
        waves = load_lottie_json(WAVES_PATH)
        if waves:
            from streamlit_lottie import st_lottie_spinner

            with st_lottie_spinner(waves, key="waves_spinner_playground"):
                run_mcp_llm_conversation(client, deployment, messages, tools_schema, function_map)
        else:
            run_mcp_llm_conversation(client, deployment, messages, tools_schema, function_map)
