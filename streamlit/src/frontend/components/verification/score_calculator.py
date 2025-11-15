"""
Score calculator module.

Combines on-chain and off-chain verification to compute a final weighted trust score.
Converts wallet summary data to on-chain scores and combines with off-chain scores
using a weighted formula (60% on-chain, 40% off-chain).
"""

from typing import Dict, Any

from .onchain_verifier import OnChainVerifier
from .offchain_verifier import OffChainVerifier


def calculate_final_trust_score(on_chain_score: float, off_chain_score: float) -> int:
    """
    Calculate the final trust score using weighted formula:
    final = 0.6 * on_chain_score + 0.4 * off_chain_score
    
    Args:
        on_chain_score: On-chain trust score (0-100)
        off_chain_score: Off-chain trust score (0-100)
    
    Returns:
        Final trust score rounded and clamped between 0-100
    """
    final = 0.85 * on_chain_score + 0.15 * off_chain_score
    # Round to nearest integer and clamp between 0-100
    final = max(0, min(100, round(final)))
    return final


def wallet_summary_to_score(wallet_summary: Dict[str, Any]) -> float:
    """
    Convert wallet summary data to a 0-100 on-chain score.
    
    Scoring factors:
    - Transaction count (0-30 pts): More transactions = higher score
    - Total value moved (0-30 pts): More value = higher score
    - Unique interactions (0-20 pts): More unique addresses = higher score
    - Wallet age (0-20 pts): Older wallet = higher score
    
    Args:
        wallet_summary: Dictionary with wallet metrics from OnChainVerifier
    
    Returns:
        On-chain score (0-100)
    """
    tx_count = wallet_summary.get("tx_count", 0)
    total_value_moved = wallet_summary.get("total_value_moved", 0.0)
    unique_interactions = wallet_summary.get("unique_interactions", 0)
    wallet_age_days = wallet_summary.get("wallet_age_days", 0.0)
    
    # Transaction count score (0-30 pts)
    # Scale: 0 tx = 0, 10 tx = 10, 50 tx = 20, 100+ tx = 30
    if tx_count == 0:
        tx_score = 0
    elif tx_count < 10:
        tx_score = tx_count
    elif tx_count < 50:
        tx_score = 10 + (tx_count - 10) * 0.25  # 10-20 range
    else:
        tx_score = min(30, 20 + (tx_count - 50) * 0.2)  # 20-30 range
    
    # Total value moved score (0-30 pts)
    # Scale: 0 ETH = 0, 1 ETH = 10, 10 ETH = 20, 100+ ETH = 30
    if total_value_moved == 0:
        value_score = 0
    elif total_value_moved < 1:
        value_score = total_value_moved * 10
    elif total_value_moved < 10:
        value_score = 10 + (total_value_moved - 1) * (10/9)  # 10-20 range
    else:
        value_score = min(30, 20 + (total_value_moved - 10) * 0.11)  # 20-30 range
    
    # Unique interactions score (0-20 pts)
    # Scale: 0 = 0, 5 = 10, 20+ = 20
    if unique_interactions == 0:
        interaction_score = 0
    elif unique_interactions < 5:
        interaction_score = unique_interactions * 2
    else:
        interaction_score = min(20, 10 + (unique_interactions - 5) * (10/15))
    
    # Wallet age score (0-20 pts)
    # Scale: 0 days = 0, 30 days = 10, 180+ days = 20
    if wallet_age_days == 0:
        age_score = 0
    elif wallet_age_days < 30:
        age_score = wallet_age_days * (10/30)
    else:
        age_score = min(20, 10 + (wallet_age_days - 30) * (10/150))
    
    total_score = tx_score + value_score + interaction_score + age_score
    return min(100, max(0, total_score))


class ScoreCalculator:
    """
    Combines on-chain and off-chain verification to compute a final trust score.
    """
    
    def __init__(self, chain: str = "ethereum"):
        """
        Initialize the score calculator with on-chain and off-chain verifiers.
        
        Args:
            chain: Blockchain to use for on-chain verification (default: "ethereum")
        """
        self.onchain_verifier = OnChainVerifier(chain=chain)
        self.offchain_verifier = OffChainVerifier()
    
    async def compute_score(
        self,
        wallet_address: str,
        user_profile: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Compute the final trust score by combining on-chain and off-chain scores.
        
        Args:
            wallet_address: Ethereum wallet address
            user_profile: Dictionary containing user profile data:
                - uploaded_files: Optional list of uploaded file objects
                - email: Optional email address string
                - phone: Optional phone number string
                - name: Optional full name string
                - social_link: Optional social media profile URL string
        
        Returns:
            Dictionary containing:
                - on_chain_score: On-chain trust score (0-100)
                - off_chain_score: Off-chain trust score (0-100)
                - final_score: Final weighted trust score (0-100)
        """
        # Get on-chain wallet summary
        wallet_summary = await self.onchain_verifier.get_wallet_summary(wallet_address)
        on_chain_score = wallet_summary_to_score(wallet_summary)
        
        # Get off-chain score
        offchain_result = self.offchain_verifier.compute_offchain_score(
            uploaded_files=user_profile.get("uploaded_files"),
            email=user_profile.get("email"),
            phone=user_profile.get("phone"),
            name=user_profile.get("name"),
            social_link=user_profile.get("social_link")
        )
        off_chain_score = offchain_result.get("total_offchain_score", 0)
        
        # Calculate final weighted score
        final_score = calculate_final_trust_score(on_chain_score, off_chain_score)
        
        return {
            "on_chain_score": round(on_chain_score),
            "off_chain_score": off_chain_score,
            "final_score": final_score
        }
