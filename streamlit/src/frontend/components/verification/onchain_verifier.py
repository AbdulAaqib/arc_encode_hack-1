"""
On-chain verification module.

Provides functionality to fetch and analyze wallet transaction data from the blockchain
using Hypersync. Extracts wallet metrics including transaction count, value moved,
unique interactions, and wallet age.
"""

import asyncio
from typing import Dict, Any

from hypersync import (
    HypersyncClient,
    ClientConfig,
    Query,
    FieldSelection,
    TransactionSelection,
    TransactionField,
)


class OnChainVerifier:
    """
    On-chain wallet verification and analysis.
    
    Fetches wallet transaction data from the blockchain using Hypersync and extracts
    key metrics including transaction count, total value moved, unique interactions,
    and wallet age.
    """
    
    def __init__(self, chain: str = "ethereum"):
        # Using the provided token directly in the class
        self.api_key = "547eb877-5324-4821-8e51-bc71dcae2659"
        self.chain = chain
        config = ClientConfig(bearer_token=self.api_key)
        self.client = HypersyncClient(config)

    async def get_wallet_summary(self, address: str) -> Dict[str, Any]:
        # Normalize address
        address = address.lower()

        # Get latest block height
        latest_block = await self.client.get_height()

        # Calculate start block (last ~6 months, ~500k blocks)
        start_block = max(0, latest_block - 500_000)

        # Create query to fetch transactions for this address
        query = Query(
            from_block=start_block,
            to_block=latest_block,
            field_selection=FieldSelection(
                transaction=[
                    TransactionField.VALUE,
                    TransactionField.TO,
                    TransactionField.FROM,
                    TransactionField.BLOCK_NUMBER,
                ]
            ),
            transactions=[
                TransactionSelection(
                    from_=[address],
                ),
                TransactionSelection(
                    to=[address],
                ),
            ],
        )

        # Execute query
        response = await self.client.get(query)
        txs = response.data.transactions

        # --- Feature Extraction ---
        tx_count = len(txs)

        # Convert value from hex string (wei) to ETH
        total_value_moved = 0.0
        for tx in txs:
            if tx.value:
                try:
                    # Value is a hex string, convert to int then to ETH
                    value_wei = int(tx.value, 16) if tx.value.startswith('0x') else int(tx.value)
                    total_value_moved += value_wei / 1e18
                except (ValueError, AttributeError):
                    pass

        unique_interactions = len(
            set(tx.to for tx in txs if tx.to)
        )

        block_numbers = [tx.block_number for tx in txs if tx.block_number is not None]
        first_seen_block = min(block_numbers) if block_numbers else None
        wallet_age = (
            (latest_block - first_seen_block) / 7200
            if first_seen_block else 0
        )  # approx days

        return {
            "address": address,
            "tx_count": tx_count,
            "total_value_moved": total_value_moved,
            "unique_interactions": unique_interactions,
            "wallet_age_days": wallet_age,
        }


# -----------------------------------------------------------
# Self-test when running the file directly
# -----------------------------------------------------------
if __name__ == "__main__":
    async def main():
        verifier = OnChainVerifier()

        test_wallet = "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"  # Binance wallet example

        print("Running on-chain verifier...\n")
        summary = await verifier.get_wallet_summary(test_wallet)

        print("=== Result ===")
        for k, v in summary.items():
            print(f"{k}: {v}")
    
    asyncio.run(main())
