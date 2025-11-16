"""Hero-style intro page with playful PawChain copy."""

from __future__ import annotations

import time
from pathlib import Path
import json
from typing import Any, Optional
from pathlib import Path
import base64

import os
from typing import Optional

import pandas as pd
import streamlit as st
from web3 import Web3

from .config import (
    ARC_RPC_ENV,
    LENDING_POOL_ADDRESS_ENV,
    LENDING_POOL_ABI_PATH_ENV,
    USDC_DECIMALS_ENV,
)
from .web3_utils import get_web3_client, load_contract_abi

INTRO_VISIT_KEY = "pawchain_intro_visited"
HERO_ASSETS = [
    Path(__file__).resolve().parents[1] / "gifs" / "sniffer_bank.gif",
]
LIQ_HISTORY_KEY = "intro_liquidity_history"


def _stream_text(text: str, delay: float = 0.04):
    """Yield text one character at a time for a typewriter effect."""

    for char in text:
        yield char
        time.sleep(delay)


def _show_hero_image(target: Optional[st.delta_generator.DeltaGenerator] = None) -> None:
    container = target or st
    for asset in HERO_ASSETS:
        if not asset.exists():
            continue
        suffix = asset.suffix.lower()
        try:
            if suffix in {".gif", ".png", ".jpg", ".jpeg"}:
                container.image(str(asset), width=220)
                return
            if suffix in {".mp4", ".mov", ".webm"}:
                container.video(str(asset))
                return
        except Exception:
            continue
    container.caption("🐾 (Hero animation unavailable)")


def _fetch_available_liquidity_usdc() -> Optional[float]:
    rpc_url = os.getenv(ARC_RPC_ENV)
    pool_address = os.getenv(LENDING_POOL_ADDRESS_ENV)
    abi_path = os.getenv(LENDING_POOL_ABI_PATH_ENV)
    decimals = int(os.getenv(USDC_DECIMALS_ENV, "6"))
    if not (rpc_url and pool_address and abi_path):
        return None
    try:
        w3 = get_web3_client(rpc_url)
        if w3 is None:
            return None
        abi = load_contract_abi(abi_path)
        if not abi:
            return None
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(pool_address), abi=abi
        )
        raw_units = contract.functions.availableLiquidity().call()
        return raw_units / (10**decimals)
    except Exception:
        return None
    client = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
    return client if client.is_connected() else None


def _resolve_session_dataframe(session_key: str) -> Optional[pd.DataFrame]:
    value = st.session_state.get(session_key)
    return value if isinstance(value, pd.DataFrame) else None


def render_intro_page() -> None:
    """Render the intro page with metric tiles and optional invoice table."""

    st.title("🐶 Sniffer Bank")

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


def _liquidity_history() -> list[float]:
    history = st.session_state.get(LIQ_HISTORY_KEY)
    if isinstance(history, list) and history:
        return history
    seed = [2.4, 2.45, 2.42, 2.46, 2.5]
    st.session_state[LIQ_HISTORY_KEY] = seed
    return seed


def _update_liquidity_history(value: Optional[float]) -> list[float]:
    history = list(_liquidity_history())
    if value is not None:
        history.append(value)
        history = history[-25:]
        st.session_state[LIQ_HISTORY_KEY] = history
    return history
    render_team_intro()



def render_intro_page() -> None:
    """Render the whimsical PawChain landing page."""

    st.title("🏠 SnifferBank Home")

    dog_col, spark_col = st.columns([0.5, 0.5])

    liquidity_value = _fetch_available_liquidity_usdc()
    liquidity_series = _update_liquidity_history(liquidity_value)
    liquidity_usdc = liquidity_series
    latest_liq = liquidity_usdc[-1]
    delta_liq = liquidity_usdc[-1] - liquidity_usdc[-2] if len(liquidity_usdc) > 1 else 0
    chart_df = pd.DataFrame({"liquidity": [round(val, 3) for val in liquidity_usdc]})

    with spark_col:
        spark_col.caption("ARC Pool Liquidity (USDC)")
        help_text = (
            "Live availableLiquidity via LendingPool contract"
            if liquidity_value is not None
            else "Env/LendingPool config missing — showing cached mock data"
        )
        spark_col.metric(
            label="Available Liquidity",
            value=f"{latest_liq:.2f} USDC",
            delta=f"{delta_liq:+.2f}",
            chart_data=chart_df,
            help=help_text,
            border=True,
        )
    
    with dog_col:
        _show_dog_gif()

    st.subheader("🐾 Welcome to Sniffer Bank")
    st.markdown(
        """
Sniffer Bank is Collie’s playground — our resident credit hound who can sniff out reliable borrowers faster than you can say “fetch.”  
We’re building cheeky, data-backed credit rails for the on-chain world, layering invoice analytics, credit registries, and wallet telemetry so lenders stay in the know while borrowers get wag-worthy experiences.

Collie’s daily routine: **Fetch invoices**, **Chase delinquent payments**, and **sit beside risk teams** with real-time insights.
        """
    )

    st.subheader("Pack Leaders")
    col1, col2, col3, col4 = st.columns(4, vertical_alignment="top")

    with col1:
        with st.container(border=True, height=320):
            st.subheader("Abdul 🐕")
            st.markdown("**Backend & Blockchain**")
            st.write(
                "Keeps the lending contracts obedient and wires wallet flows so every bridge prompt feels like a belly rub."
            )
            st.markdown(
                "🔗 [GitHub](https://github.com/AbdulAaqib) | 💼 [LinkedIn](https://www.linkedin.com/in/abdulaaqib/)"
            )

    with col2:
        with st.container(border=True, height=320):
            st.subheader("Junaid 🔧")
            st.markdown("**DevOps & Engineering Wrangler**")
            st.write(
                "Keeps infra leashes tight, deployments zoomie-free, and Streamlit sessions hydrated for every fetch request."
            )
            st.markdown(
                "🔗 [GitHub](https://github.com/Junaid2005) | 💼 [LinkedIn](https://www.linkedin.com/in/junaid-mohammad-4a4091260/)"
            )

    with col3:
        with st.container(border=True, height=320):
            st.subheader("Sukhran 🛠️")
            st.markdown("**Backend & Blockchain**")
            st.write(
                "Former chew-toy engineer, now architecting ledgers Collie trusts for borrower scoring and ARC liquidity."
            )
            st.markdown(
                "💼 [LinkedIn](https://www.linkedin.com/in/mohammed-talat-28064a1b2/)"
            )

    with col4:
        with st.container(border=True, height=320):
            st.subheader("Walid 🦮")
            st.markdown("**Lead Strategy**")
            st.write(
                "Decides which hydrants we conquer next, pairing market instincts with Collie-approved borrower journeys."
            )
            st.markdown("💼 [LinkedIn](https://www.linkedin.com/in/walid-m-155819267/)")

    st.divider()
    st.info(
        "Curious where to start? Hop into the Chatbot tab, connect MetaMask on Arc Testnet, and ask Doggo for a guided fetch mission."
    )

def render_team_intro() -> None:
    video_path = Path(__file__).resolve().parents[1] / "lottie_files" / "collie-intro.mp4"
    if video_path.exists():
        video_b64 = _read_file_base64(video_path)
        if video_b64:
            st.markdown(
                f"""
                ### 🐾 Welcome to Sniffer Bank
                <video autoplay loop muted playsinline style="border-radius:20px;display:block;margin:0 auto 1.5rem auto; margin-bottom: 2rem;">
                  <source src="data:video/mp4;base64,{video_b64}" type="video/mp4">
                </video>
                """,
                unsafe_allow_html=True,
            )

    st.markdown(
        """
        Sniffer Bank is Collie’s playground—our resident credit hound who can sniff out reliable borrowers faster
        than you can say “fetch.” We’re building cheeky, data-backed credit rails for the on-chain world, layering
        invoice analytics, credit registries, and wallet telemetry so lenders stay in the know while borrowers
        get wag-worthy experiences.
        """
    )

    st.info(
        "Collie’s daily routine: Fetch invoices, Chase delinquent payments, and sit right beside risk teams with real-time insights."
    )

    st.markdown("#### Pack Leaders")
    col1, col2 = st.columns(2)
    with col2:
        st.markdown(
            """
            - **Junaid — DevOps & Engineering Wrangler**  
              Keeps infra leashes tight and deployments zoomie-free.
            - **Walid — Lead Strategy**  
              Decides which hydrants we conquer next.
            """
        )
    with col1:
        st.markdown(
            """
            - **Sukhran — Backend & Blockchain**  
              Former chew toy engineer, now architecting ledgers Collie trusts.
            - **Abdul — Frontend & Blockchain**  
              Gives the doghouse its glow-up while wiring wallet flows.
            """
        )


def _read_file_base64(file_path: Path) -> Optional[str]:
    try:
        with file_path.open("rb") as file:
            return base64.b64encode(file.read()).decode("utf-8")
    except Exception:
        return None

def _show_dog_gif() -> None:
    video_path = Path(__file__).resolve().parents[1] / "lottie_files" / "collie-intro.mp4"
    if video_path.exists():
        video_b64 = _read_file_base64(video_path)
        if video_b64:
            st.markdown(
                f"""
                <video autoplay loop muted playsinline style="display:block;border-radius:20px;margin:0 auto 1.5rem auto; margin-bottom: 2rem;">
                  <source src="data:video/mp4;base64,{video_b64}" type="video/mp4">
                </video>
                """,
                unsafe_allow_html=True,
            )