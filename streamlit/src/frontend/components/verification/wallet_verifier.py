"""
Wallet verification module.

Provides two-level wallet verification:
1. Format validation (address structure, length, hex format)
2. On-chain existence check (verifies wallet has on-chain activity)
"""

import asyncio
from typing import Dict, Any, Tuple

from hypersync import (
    HypersyncClient,
    ClientConfig,
    Query,
    FieldSelection,
    TransactionSelection,
    TransactionField,
)


class WalletVerifier:
    """
    Two-level wallet verification system:
    - Level 1: Format validation (starts with 0x, length 42, hex-only, not zero address)
    - Level 2: On-chain existence check using Hypersync
    """
    
    def __init__(self, chain: str = "ethereum"):
        """
        Initialize the wallet verifier with Hypersync client.
        
        Args:
            chain: Blockchain to use for verification (default: "ethereum")
        """
        self.api_key = "547eb877-5324-4821-8e51-bc71dcae2659"
        self.chain = chain
        config = ClientConfig(bearer_token=self.api_key)
        self.client = HypersyncClient(config)
    
    def _validate_format(self, address: str) -> Tuple[bool, str]:
        """
        Level 1: Format validation.
        
        Checks:
        - Starts with 0x
        - Length == 42
        - Hex-only characters
        - Not the zero-address
        
        Args:
            address: Wallet address to validate
            
        Returns:
            Tuple of (is_valid: bool, reason: str)
        """
        # Check if starts with 0x
        if not address.startswith("0x"):
            return False, "Address must start with '0x'"
        
        # Check length
        if len(address) != 42:
            return False, f"Address length must be 42 characters, got {len(address)}"
        
        # Check hex-only characters (after 0x)
        hex_part = address[2:]
        try:
            int(hex_part, 16)
        except ValueError:
            return False, "Address contains non-hexadecimal characters"
        
        # Check not zero-address
        zero_address = "0x0000000000000000000000000000000000000000"
        if address.lower() == zero_address:
            return False, "Address cannot be the zero address"
        
        return True, "Format validation passed"
    
    async def _check_onchain_existence(self, address: str) -> Tuple[bool, str]:
        """
        Level 2: On-chain existence check using Hypersync.
        
        Queries transactions to/from the address to determine if it has
        any on-chain activity.
        
        Args:
            address: Wallet address to check (will be normalized to lowercase)
            
        Returns:
            Tuple of (has_activity: bool, reason: str)
        """
        # Normalize address to lowercase
        address = address.lower()
        
        try:
            # Get latest block height
            latest_block = await self.client.get_height()
            
            # Create query to check for any transactions to/from this address
            query = Query(
                from_block=0,
                to_block=latest_block,
                field_selection=FieldSelection(
                    transaction=[
                        TransactionField.FROM,
                        TransactionField.TO
                    ]
                ),
                transactions=[
                    TransactionSelection(to=[address]),
                    TransactionSelection(from_=[address])
                ]
            )
            
            # Execute query
            response = await self.client.get(query)
            
            # Check if there are any transactions
            tx_count = len(response.data.transactions) if response.data.transactions else 0
            
            if tx_count > 0:
                return True, f"Wallet has on-chain activity ({tx_count} transaction(s) found)"
            else:
                return False, "Wallet has no on-chain activity"
                
        except Exception as e:
            return False, f"Error checking on-chain existence: {str(e)}"
    
    async def verify_wallet(self, address: str) -> Dict[str, Any]:
        """
        Main verification method that performs both format and on-chain checks.
        
        Args:
            address: Wallet address to verify
            
        Returns:
            Dictionary containing:
                - valid_format: bool - Whether format validation passed
                - active_onchain: bool - Whether wallet has on-chain activity
                - reason: str - Explanation of the result
        """
        # Level 1: Format validation
        format_valid, format_reason = self._validate_format(address)
        
        if not format_valid:
            return {
                "valid_format": False,
                "active_onchain": False,
                "reason": format_reason
            }
        
        # Level 2: On-chain existence check
        onchain_active, onchain_reason = await self._check_onchain_existence(address)
        
        # Combine results
        if format_valid and onchain_active:
            reason = f"Wallet is valid: {format_reason}. {onchain_reason}"
        elif format_valid and not onchain_active:
            reason = f"Wallet format is valid but {onchain_reason.lower()}"
        else:
            reason = format_reason
        
        return {
            "valid_format": format_valid,
            "active_onchain": onchain_active,
            "reason": reason
        }


# -----------------------------------------------------------
# Self-test when running the file directly
# -----------------------------------------------------------
if __name__ == "__main__":
    async def main():
        print("Testing Wallet Verifier...\n")
        
        # Initialize verifier
        verifier = WalletVerifier()
        
        # Test wallet address
        test_wallet = "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"
        
        print(f"Testing wallet: {test_wallet}\n")
        print("Running verification...\n")
        
        result = await verifier.verify_wallet(test_wallet)
        
        print("=== Verification Results ===")
        print(f"Valid Format: {result['valid_format']}")
        print(f"Active On-chain: {result['active_onchain']}")
        print(f"Reason: {result['reason']}")
        print()
        
        # Additional test cases
        print("\n=== Additional Test Cases ===")
        
        # Test invalid format (too short)
        print("\n1. Testing invalid format (too short):")
        invalid_short = "0x123"
        result_short = await verifier.verify_wallet(invalid_short)
        print(f"   Result: {result_short['reason']}")
        
        # Test invalid format (no 0x)
        print("\n2. Testing invalid format (no 0x):")
        invalid_no_0x = "742d35Cc6634C0532925a3b844Bc454e4438f44e"
        result_no_0x = await verifier.verify_wallet(invalid_no_0x)
        print(f"   Result: {result_no_0x['reason']}")
        
        # Test zero address
        print("\n3. Testing zero address:")
        zero_addr = "0x0000000000000000000000000000000000000000"
        result_zero = await verifier.verify_wallet(zero_addr)
        print(f"   Result: {result_zero['reason']}")
    
    asyncio.run(main())

