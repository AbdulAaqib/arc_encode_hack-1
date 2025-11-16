"""Borrower-initiated CCTP bridge tools for funds already in borrower wallet."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st
from web3 import Web3
from eth_account import Account

from ..cctp_bridge import (
    POLYGON_AMOY_CHAIN_ID,
    BridgeError,
    ARC_USDC_ADDRESS,
    TOKEN_MESSENGER_ADDRESS,
    MESSAGE_TRANSMITTER_ADDRESS,
    POLYGON_DOMAIN_ID,
    ARC_DOMAIN_ID,
    DEFAULT_MIN_FINALITY,
    DEFAULT_MAX_FEE_BUFFER,
    ARC_TX_EXPLORER_TEMPLATE,
    ERC20_ABI,
    TOKEN_MESSENGER_ABI,
    _parse_usdc_amount,
    _address_to_bytes32,
    _normalise_tx_hash,
    _init_web3,
    _apply_gas_values,
    poll_attestation,
    _ensure_hex_bytes,
    _encode_receive_message_call_data,
)
from ..config import (
    ARC_RPC_ENV,
    GAS_LIMIT_ENV,
    GAS_PRICE_GWEI_ENV,
)
from ..toolkit_lib.messages import tool_error, tool_success
from ..mcp_lib.constants import (
    MCP_BORROWER_BRIDGE_SESSION_KEY,
    ATTESTATION_POLL_INTERVAL,
    ATTESTATION_TIMEOUT,
    ATTESTATION_INITIAL_TIMEOUT,
)


def _parse_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(value, 0)
    except ValueError:
        return None


def _parse_gas_price(gwei_value: Optional[str]) -> Optional[int]:
    if not gwei_value:
        return None
    try:
        decimal_value = Decimal(gwei_value)
        return int(decimal_value * Decimal(1_000_000_000))
    except (InvalidOperation, ValueError):
        return None


def _bridge_logs_payload(logs: List[str]) -> Dict[str, Any]:
    return {"logs": logs[-40:], "logCount": len(logs)}


def build_borrower_bridge_toolkit() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build tools for borrower to bridge their own USDC to Polygon."""
    tools: List[Dict[str, Any]] = []
    handlers: Dict[str, Any] = {}

    def register(
        name: str, description: str, parameters: Dict[str, Any], handler: Any
    ) -> None:
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            }
        )
        handlers[name] = handler

    def prepare_borrower_bridge_tool(
        polygon_address: str, amount: str
    ) -> str:
        """Prepare a MetaMask transaction for borrower to bridge their own USDC."""
        
        arc_rpc_url = os.getenv(ARC_RPC_ENV)
        if not arc_rpc_url:
            return tool_error("ARC RPC URL not configured.")
        
        gas_limit = _parse_int(os.getenv(GAS_LIMIT_ENV))
        gas_price_wei = _parse_gas_price(os.getenv(GAS_PRICE_GWEI_ENV))
        
        try:
            amount_dec, amount_base_units = _parse_usdc_amount(amount)
        except BridgeError as exc:
            return tool_error(str(exc))
        
        if not Web3.is_address(polygon_address):
            return tool_error("Invalid Polygon address.")
        polygon_checksum = Web3.to_checksum_address(polygon_address)
        
        try:
            w3 = _init_web3(arc_rpc_url)
            chain_id = w3.eth.chain_id
        except BridgeError as exc:
            return tool_error(str(exc))
        
        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(ARC_USDC_ADDRESS), abi=ERC20_ABI
        )
        messenger = w3.eth.contract(
            address=Web3.to_checksum_address(TOKEN_MESSENGER_ADDRESS),
            abi=TOKEN_MESSENGER_ABI,
        )
        
        # Build the approve transaction for USDC
        # Directly encode without any simulation
        from eth_abi import encode
        
        # Function selector for approve(address,uint256)
        approve_selector = Web3.keccak(text="approve(address,uint256)")[:4]
        approve_params = encode(
            ['address', 'uint256'],
            [TOKEN_MESSENGER_ADDRESS, amount_base_units]
        )
        approve_tx_data = "0x" + approve_selector.hex() + approve_params.hex()
        
        # Build the depositForBurn transaction
        max_fee_base_units = amount_base_units - DEFAULT_MAX_FEE_BUFFER
        if max_fee_base_units <= 0:
            max_fee_base_units = 1
        
        # Function selector for depositForBurn
        burn_selector = Web3.keccak(text="depositForBurn(uint256,uint32,bytes32,address,bytes32,uint256,uint32)")[:4]
        burn_params = encode(
            ['uint256', 'uint32', 'bytes32', 'address', 'bytes32', 'uint256', 'uint32'],
            [
                amount_base_units,
                POLYGON_DOMAIN_ID,
                _address_to_bytes32(polygon_checksum),
                Web3.to_checksum_address(ARC_USDC_ADDRESS),
                bytes(32),
                max_fee_base_units,
                DEFAULT_MIN_FINALITY
            ]
        )
        burn_tx_data = "0x" + burn_selector.hex() + burn_params.hex()
        
        # Store bridge details in session for later reference
        bridge_state = {
            "amount_usdc": format(amount_dec, "f"),
            "amount_base_units": amount_base_units,
            "polygon_address": polygon_checksum,
            "status": "pending_approval",
        }
        st.session_state[MCP_BORROWER_BRIDGE_SESSION_KEY] = bridge_state
        
        # Return the transaction data in the format that triggers MetaMask
        # The conversation.py handler will detect the metamask field and trigger the popup
        return tool_success({
            "bridge": bridge_state,
            "metamask": {
                "tx_request": {
                    "to": ARC_USDC_ADDRESS,
                    "data": approve_tx_data,
                },
                "action": "eth_sendTransaction",
                "chainId": chain_id,
                "hint": f"Approve USDC spending ({amount_dec} USDC)",
            },
            "message": (
                f"Triggering MetaMask for CCTP bridge:\n"
                f"1. First popup: Approve USDC spending ({amount_dec} USDC)\n"
                f"2. After approval, you'll need to call the bridge transaction\n\n"
                f"The approve popup should appear now."
            ),
            "next_step": {
                "burn_tx": {
                    "to": TOKEN_MESSENGER_ADDRESS,
                    "data": burn_tx_data,
                    "hint": f"Bridge {amount_dec} USDC to Polygon",
                }
            }
        })

    register(
        "prepareBorrowerBridge",
        "Prepare MetaMask transaction to approve USDC spending for CCTP bridge.",
        {
            "type": "object",
            "properties": {
                "polygon_address": {
                    "type": "string",
                    "description": "Destination Polygon wallet address.",
                },
                "amount": {
                    "type": "string",
                    "description": "Amount of USDC to bridge (e.g., 0.10).",
                },
            },
            "required": ["polygon_address", "amount"],
        },
        prepare_borrower_bridge_tool,
    )
    
    def execute_borrower_burn_tool() -> str:
        """Execute the burn transaction after approval."""
        bridge_state = st.session_state.get(MCP_BORROWER_BRIDGE_SESSION_KEY)
        if not bridge_state:
            return tool_error("No bridge session found. Call prepareBorrowerBridge first.")
        
        polygon_address = bridge_state.get("polygon_address")
        amount_dec = Decimal(bridge_state.get("amount_usdc", "0"))
        amount_base_units = bridge_state.get("amount_base_units", 0)
        
        if not polygon_address:
            return tool_error("No polygon address in bridge state.")
        
        # Build the depositForBurn transaction data
        from eth_abi import encode
        
        max_fee_base_units = amount_base_units - DEFAULT_MAX_FEE_BUFFER
        if max_fee_base_units <= 0:
            max_fee_base_units = 1
        
        burn_selector = Web3.keccak(text="depositForBurn(uint256,uint32,bytes32,address,bytes32,uint256,uint32)")[:4]
        burn_params = encode(
            ['uint256', 'uint32', 'bytes32', 'address', 'bytes32', 'uint256', 'uint32'],
            [
                amount_base_units,
                POLYGON_DOMAIN_ID,
                _address_to_bytes32(Web3.to_checksum_address(polygon_address)),
                Web3.to_checksum_address(ARC_USDC_ADDRESS),
                bytes(32),
                max_fee_base_units,
                DEFAULT_MIN_FINALITY
            ]
        )
        burn_tx_data = "0x" + burn_selector.hex() + burn_params.hex()
        
        # Update bridge state
        bridge_state["status"] = "pending_burn"
        st.session_state[MCP_BORROWER_BRIDGE_SESSION_KEY] = bridge_state
        
        # Return in format that triggers MetaMask
        return tool_success({
            "bridge": bridge_state,
            "metamask": {
                "tx_request": {
                    "to": TOKEN_MESSENGER_ADDRESS,
                    "data": burn_tx_data,
                },
                "action": "eth_sendTransaction",
                "chainId": 5042002,  # ARC testnet
                "hint": f"Execute CCTP bridge ({amount_dec} USDC to Polygon)",
            },
            "message": f"Now executing the CCTP bridge to burn {amount_dec} USDC and send to Polygon. Please approve the transaction."
        })
    
    register(
        "executeBorrowerBurn",
        "Execute the CCTP burn transaction after USDC approval.",
        {"type": "object", "properties": {}, "required": []},
        lambda: execute_borrower_burn_tool(),
    )

    def check_borrower_usdc_balance_tool(borrower_address: Optional[str] = None, borrower_wallet_address: Optional[str] = None) -> str:
        """Check borrower's USDC balance on ARC."""
        # Accept both parameter names for compatibility
        address = borrower_address or borrower_wallet_address
        if not address:
            return tool_error("Borrower address is required.")
        
        arc_rpc_url = os.getenv(ARC_RPC_ENV)
        if not arc_rpc_url:
            return tool_error("ARC RPC URL not configured.")
        
        if not Web3.is_address(address):
            return tool_error("Invalid borrower address.")
        borrower_checksum = Web3.to_checksum_address(address)
        
        try:
            w3 = _init_web3(arc_rpc_url)
        except BridgeError as exc:
            return tool_error(str(exc))
        
        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(ARC_USDC_ADDRESS), abi=ERC20_ABI
        )
        
        try:
            balance = usdc.functions.balanceOf(borrower_checksum).call()
            balance_decimal = Decimal(balance) / Decimal(10 ** 6)  # USDC has 6 decimals
            
            return tool_success({
                "borrower": borrower_checksum,
                "usdc_balance": balance,
                "usdc_balance_human": format(balance_decimal, "f"),
            })
        except Exception as exc:
            return tool_error(f"Failed to check balance: {exc}")

    def check_usdc_allowance_tool(owner_address: Optional[str] = None, spender_address: Optional[str] = None) -> str:
        """Check USDC allowance for TokenMessenger."""
        address = owner_address
        if not address:
            return tool_error("Owner address is required.")
        
        arc_rpc_url = os.getenv(ARC_RPC_ENV)
        if not arc_rpc_url:
            return tool_error("ARC RPC URL not configured.")
        
        try:
            w3 = _init_web3(arc_rpc_url)
        except BridgeError as exc:
            return tool_error(str(exc))
        
        usdc = w3.eth.contract(
            address=Web3.to_checksum_address(ARC_USDC_ADDRESS), abi=ERC20_ABI
        )
        
        spender = spender_address or TOKEN_MESSENGER_ADDRESS
        
        try:
            owner_checksum = Web3.to_checksum_address(address)
            spender_checksum = Web3.to_checksum_address(spender)
            allowance = usdc.functions.allowance(owner_checksum, spender_checksum).call()
            allowance_decimal = Decimal(allowance) / Decimal(10 ** 6)  # USDC has 6 decimals
            
            return tool_success({
                "owner": owner_checksum,
                "spender": spender_checksum,
                "allowance": allowance,
                "allowance_human": format(allowance_decimal, "f"),
                "is_token_messenger": spender_checksum.lower() == TOKEN_MESSENGER_ADDRESS.lower(),
            })
        except Exception as exc:
            return tool_error(f"Failed to check allowance: {exc}")

    register(
        "checkUsdcAllowance",
        "Check USDC allowance for TokenMessenger or specified spender.",
        {
            "type": "object",
            "properties": {
                "owner_address": {
                    "type": "string",
                    "description": "Owner wallet address.",
                },
                "spender_address": {
                    "type": "string",
                    "description": "Spender address (defaults to TokenMessenger).",
                },
            },
            "required": ["owner_address"],
        },
        check_usdc_allowance_tool,
    )
    
    register(
        "checkBorrowerUsdcBalance",
        "Check the borrower's USDC balance on ARC chain.",
        {
            "type": "object",
            "properties": {
                "borrower_address": {
                    "type": "string",
                    "description": "Borrower wallet address.",
                },
                "borrower_wallet_address": {
                    "type": "string",
                    "description": "Borrower wallet address (alternative parameter name).",
                },
            },
            "required": [],
        },
        check_borrower_usdc_balance_tool,
    )

    def get_borrower_bridge_state_tool() -> str:
        state = st.session_state.get(MCP_BORROWER_BRIDGE_SESSION_KEY)
        if not state:
            return tool_error("No borrower bridge session found.")
        return tool_success({"bridge": state})

    register(
        "getBorrowerBridgeState",
        "Return the current borrower-initiated bridge session state.",
        {"type": "object", "properties": {}, "required": []},
        lambda: get_borrower_bridge_state_tool(),
    )

    def clear_borrower_bridge_tool() -> str:
        st.session_state.pop(MCP_BORROWER_BRIDGE_SESSION_KEY, None)
        return tool_success({"message": "Cleared borrower bridge session."})

    register(
        "clearBorrowerBridgeState",
        "Clear the stored borrower bridge session.",
        {"type": "object", "properties": {}, "required": []},
        lambda: clear_borrower_bridge_tool(),
    )

    def store_burn_tx_tool(burn_tx_hash: str) -> str:
        """Store the burn transaction hash after borrower executes bridge."""
        if not burn_tx_hash:
            return tool_error("Burn transaction hash is required.")
        
        burn_hash_normalized = _normalise_tx_hash(burn_tx_hash)
        
        # Update bridge state with burn tx
        bridge_state = st.session_state.get(MCP_BORROWER_BRIDGE_SESSION_KEY, {})
        bridge_state["burn_tx_hash"] = burn_hash_normalized
        bridge_state["burn_tx_explorer"] = ARC_TX_EXPLORER_TEMPLATE.format(tx_hash=burn_hash_normalized)
        bridge_state["status"] = "burn_complete_awaiting_attestation"
        st.session_state[MCP_BORROWER_BRIDGE_SESSION_KEY] = bridge_state
        
        return tool_success({
            "bridge": bridge_state,
            "message": "Burn transaction recorded. Now polling for Circle attestation..."
        })

    register(
        "storeBorrowerBurnTx",
        "Store the burn transaction hash after borrower completes the bridge transaction.",
        {
            "type": "object",
            "properties": {
                "burn_tx_hash": {
                    "type": "string",
                    "description": "Transaction hash from the depositForBurn transaction.",
                },
            },
            "required": ["burn_tx_hash"],
        },
        store_burn_tx_tool,
    )

    def poll_borrower_attestation_tool() -> str:
        """Poll Circle for attestation after borrower's burn transaction."""
        bridge_state = st.session_state.get(MCP_BORROWER_BRIDGE_SESSION_KEY)
        if not bridge_state:
            return tool_error("No bridge session found.")
        
        burn_tx_hash = bridge_state.get("burn_tx_hash")
        if not burn_tx_hash:
            return tool_error("No burn transaction hash found. Call storeBorrowerBurnTx first.")
        
        arc_rpc_url = os.getenv(ARC_RPC_ENV)
        if not arc_rpc_url:
            return tool_error("ARC RPC URL not configured.")
        
        logs: List[str] = []
        try:
            # Poll for attestation
            message, attestation = poll_attestation(
                ARC_DOMAIN_ID,
                burn_tx_hash,
                interval=ATTESTATION_POLL_INTERVAL,
                timeout=30,  # Quick check, don't wait too long
                log=lambda msg: logs.append(str(msg)),
            )
            
            message_hex = _ensure_hex_bytes(message, "message")
            attestation_hex = _ensure_hex_bytes(attestation, "attestation")
            
            # Generate the Polygon mint call data
            w3 = _init_web3(arc_rpc_url)
            call_data = _encode_receive_message_call_data(w3, message_hex, attestation_hex)
            
            # Update bridge state
            bridge_state["message_hex"] = message_hex
            bridge_state["attestation_hex"] = attestation_hex
            bridge_state["receive_message_call_data"] = call_data
            bridge_state["status"] = "ready_to_mint"
            st.session_state[MCP_BORROWER_BRIDGE_SESSION_KEY] = bridge_state
            
            return tool_success({
                "bridge": bridge_state,
                "attestation_ready": True,
                "message": "Attestation received! Ready to mint on Polygon.",
                **_bridge_logs_payload(logs)
            })
            
        except BridgeError as exc:
            # Attestation not ready yet
            bridge_state["status"] = "waiting_for_attestation"
            st.session_state[MCP_BORROWER_BRIDGE_SESSION_KEY] = bridge_state
            
            return tool_success({
                "bridge": bridge_state,
                "attestation_ready": False,
                "message": f"Attestation not ready yet: {exc}. Keep polling...",
                **_bridge_logs_payload(logs)
            })

    register(
        "pollBorrowerAttestation",
        "Poll Circle for attestation after borrower's burn transaction. Keep calling until attestation_ready is true.",
        {"type": "object", "properties": {}, "required": []},
        lambda: poll_borrower_attestation_tool(),
    )

    def prepare_polygon_mint_for_borrower_tool() -> str:
        """Prepare the Polygon mint transaction for borrower after attestation."""
        bridge_state = st.session_state.get(MCP_BORROWER_BRIDGE_SESSION_KEY)
        if not bridge_state:
            return tool_error("No bridge session found.")
        
        call_data = bridge_state.get("receive_message_call_data")
        if not call_data:
            return tool_error("No mint call data available. Poll for attestation first.")
        
        polygon_address = bridge_state.get("polygon_address")
        amount_usdc = bridge_state.get("amount_usdc")
        
        # Update status
        bridge_state["status"] = "pending_polygon_mint"
        st.session_state[MCP_BORROWER_BRIDGE_SESSION_KEY] = bridge_state
        
        return tool_success({
            "bridge": bridge_state,
            "metamask": {
                "tx_request": {
                    "to": MESSAGE_TRANSMITTER_ADDRESS,
                    "data": call_data,
                },
                "action": "eth_sendTransaction",
                "chainId": POLYGON_AMOY_CHAIN_ID,
                "hint": f"Mint {amount_usdc} USDC on Polygon",
                "from": polygon_address,
            },
            "message": (
                f"Ready to mint {amount_usdc} USDC on Polygon!\n"
                f"Please switch to Polygon network and approve the transaction in MetaMask."
            ),
        })

    register(
        "preparePolygonMintForBorrower",
        "Prepare the Polygon mint transaction for borrower after Circle attestation is ready.",
        {"type": "object", "properties": {}, "required": []},
        lambda: prepare_polygon_mint_for_borrower_tool(),
    )

    def complete_borrower_bridge_tool(mint_tx_hash: Optional[str] = None) -> str:
        """Mark the borrower bridge as complete after Polygon mint."""
        bridge_state = st.session_state.get(MCP_BORROWER_BRIDGE_SESSION_KEY)
        if not bridge_state:
            return tool_error("No bridge session found.")
        
        if mint_tx_hash:
            bridge_state["mint_tx_hash"] = mint_tx_hash
            bridge_state["mint_tx_explorer"] = f"https://amoy.polygonscan.com/tx/{mint_tx_hash}"
        
        bridge_state["status"] = "complete"
        st.session_state[MCP_BORROWER_BRIDGE_SESSION_KEY] = bridge_state
        
        amount_usdc = bridge_state.get("amount_usdc", "?")
        polygon_address = bridge_state.get("polygon_address", "?")
        
        return tool_success({
            "bridge": bridge_state,
            "message": (
                f"✅ Bridge complete! {amount_usdc} USDC has been successfully bridged to {polygon_address} on Polygon.\n"
                f"Burn tx: {bridge_state.get('burn_tx_explorer', 'N/A')}\n"
                f"Mint tx: {bridge_state.get('mint_tx_explorer', 'N/A')}"
            ),
        })

    register(
        "completeBorrowerBridge",
        "Mark the borrower bridge as complete after successful Polygon mint.",
        {
            "type": "object",
            "properties": {
                "mint_tx_hash": {
                    "type": "string",
                    "description": "Optional Polygon mint transaction hash.",
                },
            },
            "required": [],
        },
        complete_borrower_bridge_tool,
    )

    return tools, handlers
