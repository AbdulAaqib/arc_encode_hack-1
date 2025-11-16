"""Intro page component showing credit metrics and invoice analytics."""

from __future__ import annotations

import json
from typing import Any, Optional

import os
import pandas as pd
import streamlit as st
from web3 import Web3
from web3.exceptions import Web3Exception


@st.cache_resource(show_spinner=False)
def get_web3_client(rpc: str) -> Optional[Web3]:
    """Return a connected Web3 client for the supplied RPC endpoint."""

    if not rpc:
        return None
    client = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
    return client if client.is_connected() else None


def _resolve_session_dataframe(session_key: str) -> Optional[pd.DataFrame]:
    value = st.session_state.get(session_key)
    return value if isinstance(value, pd.DataFrame) else None


def render_intro_page() -> None:
    """Render the intro page with metric tiles and optional invoice table."""

    st.title("🐶 PawChain Capital Credit Dashboard")

    env_rpc = os.getenv("ARC_TESTNET_RPC_URL", "https://rpc.testnet.arc.network")
    rpc_url = st.session_state.get("rpc_url") or env_rpc
    wallet_address = st.session_state.get("wallet_address")
    contract_address = st.session_state.get("contract_address")
    abi_text = st.session_state.get("contract_abi")
    df = _resolve_session_dataframe("invoice_df")

    w3 = get_web3_client(rpc_url) if rpc_url else None
    if rpc_url and not w3:
        st.warning("Unable to connect to the provided RPC endpoint. Double-check the URL or network status.")

    balance = _fetch_wallet_balance(w3, wallet_address) if w3 and wallet_address else None
    avg_delay, invoice_count = _compute_invoice_metrics(df)
    credit_score = _fetch_credit_score(w3, wallet_address, contract_address, abi_text)

    col1, col2, col3 = st.columns(3)
    col1.metric("Wallet Balance (USDC)", f"{balance:.2f}" if balance is not None else "—")
    col2.metric("Avg Payment Delay (days)", f"{avg_delay:.1f}" if avg_delay is not None else "—")
    col3.metric("Invoice Count", invoice_count if invoice_count is not None else "—")

    if credit_score is not None:
        st.markdown("### Credit Registry Snapshot")
        st.json({"creditScore": credit_score[0], "metadata": credit_score[1:], "raw": credit_score})

    if df is not None:
        st.markdown("### Invoice Overview")
        st.dataframe(df)

        numeric_cols = df.select_dtypes(include=["number"]).columns
        if len(numeric_cols) > 0:
            with st.expander("Numeric column trends"):
                st.line_chart(df[numeric_cols])


def _fetch_wallet_balance(web3_client: Web3, wallet_address: str) -> Optional[float]:
    try:
        checksum_address = Web3.to_checksum_address(wallet_address)
        raw_balance = web3_client.eth.get_balance(checksum_address)
        return raw_balance / (10**18)
    except ValueError:
        st.error("Wallet address is invalid. Please enter a valid checksum address.")
    except Web3Exception as exc:  # pragma: no cover - UI feedback only
        st.error(f"Failed to fetch balance: {exc}")
    except Exception as exc:  # pragma: no cover - UI feedback only
        st.error(f"Unexpected error fetching balance: {exc}")
    return None


def _compute_invoice_metrics(df: Optional[pd.DataFrame]) -> tuple[Optional[float], Optional[int]]:
    if df is None:
        return None, None
    invoice_count = len(df)
    avg_delay = float(df["days_to_payment"].mean()) if "days_to_payment" in df.columns else None
    return avg_delay, invoice_count


def _fetch_credit_score(
    web3_client: Optional[Web3],
    wallet_address: Optional[str],
    contract_address: Optional[str],
    abi_text: Optional[str],
) -> Optional[Any]:
    if not (web3_client and wallet_address and contract_address and abi_text):
        return None

    try:
        abi = json.loads(abi_text)
        contract = web3_client.eth.contract(address=Web3.to_checksum_address(contract_address), abi=abi)
        return contract.functions.scores(Web3.to_checksum_address(wallet_address)).call()
    except json.JSONDecodeError:
        st.error("ABI is not valid JSON. Please paste a valid ABI array.")
    except ValueError as exc:  # includes bad addresses
        st.error(f"Contract interaction error: {exc}")
    except Web3Exception as exc:  # pragma: no cover - UI feedback only
        st.error(f"Unable to query contract: {exc}")
    except Exception as exc:  # pragma: no cover - UI feedback only
        st.error(f"Unexpected contract error: {exc}")
    return None

