# TrustMintSBT-focused configuration for MCP UI
# These env keys keep names simple and aligned with blockchain_commands.txt

# RPC URL (Arc testnet)
ARC_RPC_ENV = "ARC_TESTNET_RPC_URL"

# Contract address and ABI path for TrustMintSBT
SBT_ADDRESS_ENV = "SBT_ADDRESS"
TRUSTMINT_SBT_ABI_PATH_ENV = "TRUSTMINT_SBT_ABI_PATH"

# Signing and gas settings
PRIVATE_KEY_ENV = "PRIVATE_KEY"
BRIDGE_PRIVATE_KEY_ENV = "ARC_OWNER_PRIVATE_KEY"
USDC_DECIMALS_ENV = "ARC_USDC_DECIMALS"  # not used by SBT tools, but kept for compatibility
GAS_LIMIT_ENV = "ARC_GAS_LIMIT"
GAS_PRICE_GWEI_ENV = "ARC_GAS_PRICE_GWEI"
POLYGON_RPC_ENV = "POLYGON_RPC"
POLYGON_PRIVATE_KEY_ENV = "POLYGON_PRIVATE_KEY"

# Helper so callers can resolve the SBT address consistently
import os
from typing import Optional, Tuple

def get_sbt_address() -> Tuple[Optional[str], str]:
    addr = os.getenv(SBT_ADDRESS_ENV)
    return (addr, SBT_ADDRESS_ENV) if addr else (None, "")
# LendingPool + USDC env keys for MCP tools
LENDING_POOL_ADDRESS_ENV = "LENDING_POOL_ADDRESS"
LENDING_POOL_ABI_PATH_ENV = "LENDING_POOL_ABI_PATH"
USDC_ADDRESS_ENV = "USDC_ADDRESS"
USDC_ABI_PATH_ENV = "USDC_ABI_PATH"


def get_lending_pool_address() -> Tuple[Optional[str], str]:
    addr = os.getenv(LENDING_POOL_ADDRESS_ENV)
    return (addr, LENDING_POOL_ADDRESS_ENV) if addr else (None, "")


def get_usdc_address() -> Tuple[Optional[str], str]:
    addr = os.getenv(USDC_ADDRESS_ENV)
    return (addr, USDC_ADDRESS_ENV) if addr else (None, "")

