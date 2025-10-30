"""
Eligibility checker module.

Calculates eligible loan amounts based on trust scores using a tiered bracket system.
Applies bonuses for wallet age, transaction count, and value moved metrics.
"""

from typing import Dict, Any, Optional


class EligibilityChecker:
    """
    Calculates eligible loan amounts based on trust scores using a tiered bracket system.
    """
    
    # Placeholder constants (easy to update later)
    MAX_LOAN_AMOUNT_USDC = 10_000  # Maximum loan amount in USDC
    MIN_TRUST_SCORE = 40  # Minimum trust score threshold (0-100)
    
    # Tiered bracket percentages
    TIER_HIGH = (80, 100, 1.0)      # 80-100: 100% of max
    TIER_MEDIUM = (60, 79, 0.75)    # 60-79: 75% of max
    TIER_LOW = (40, 59, 0.5)        # 40-59: 50% of max
    
    # Bonus thresholds
    WALLET_AGE_THRESHOLD_DAYS = 180
    WALLET_AGE_BONUS = 0.10  # +10%
    
    TX_COUNT_THRESHOLD = 50
    TX_COUNT_BONUS = 0.05  # +5%
    
    VALUE_MOVED_THRESHOLD_ETH = 10.0
    VALUE_MOVED_BONUS = 0.05  # +5%
    
    MAX_TOTAL_BONUS = 0.20  # Cap at +20%
    
    def __init__(self, max_loan_amount_usdc: Optional[int] = None, min_trust_score: Optional[int] = None):
        """
        Initialize the eligibility checker.
        
        Args:
            max_loan_amount_usdc: Maximum loan amount in USDC (default: placeholder value)
            min_trust_score: Minimum trust score threshold (default: placeholder value)
        """
        self.max_loan_amount_usdc = max_loan_amount_usdc or self.MAX_LOAN_AMOUNT_USDC
        self.min_trust_score = min_trust_score or self.MIN_TRUST_SCORE
    
    def calculate_eligible_amount(
        self,
        trust_score: int,
        wallet_summary: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Calculate the eligible loan amount based on trust score and wallet metrics.
        
        Args:
            trust_score: Trust score (0-100)
            wallet_summary: Optional wallet summary from OnChainVerifier containing:
                - wallet_age_days: Wallet age in days
                - tx_count: Transaction count
                - total_value_moved: Total value moved in ETH
        
        Returns:
            Dictionary containing:
                - eligible: Boolean indicating if user is eligible
                - amount_usdc: Eligible loan amount in USDC (0 if not eligible)
                - reason: Reason for eligibility/ineligibility
                - factors_applied: List of factors that influenced the calculation
        """
        factors_applied = []
        
        # Check minimum threshold
        if trust_score < self.min_trust_score:
            return {
                "eligible": False,
                "amount_usdc": 0,
                "reason": f"Trust score {trust_score} below minimum threshold of {self.min_trust_score}",
                "factors_applied": factors_applied
            }
        
        # Determine base amount from tiered brackets
        base_percentage = 0.0
        tier_name = ""
        
        if self.TIER_HIGH[0] <= trust_score <= self.TIER_HIGH[1]:
            base_percentage = self.TIER_HIGH[2]
            tier_name = "High (80-100)"
        elif self.TIER_MEDIUM[0] <= trust_score <= self.TIER_MEDIUM[1]:
            base_percentage = self.TIER_MEDIUM[2]
            tier_name = "Medium (60-79)"
        elif self.TIER_LOW[0] <= trust_score <= self.TIER_LOW[1]:
            base_percentage = self.TIER_LOW[2]
            tier_name = "Low (40-59)"
        
        base_amount = self.max_loan_amount_usdc * base_percentage
        factors_applied.append(f"Base tier ({tier_name}): {base_percentage * 100:.0f}% of max")
        
        # Calculate bonuses from wallet metrics
        total_bonus = 0.0
        
        if wallet_summary:
            wallet_age_days = wallet_summary.get("wallet_age_days", 0.0)
            tx_count = wallet_summary.get("tx_count", 0)
            total_value_moved = wallet_summary.get("total_value_moved", 0.0)
            
            # Wallet age bonus
            if wallet_age_days > self.WALLET_AGE_THRESHOLD_DAYS:
                total_bonus += self.WALLET_AGE_BONUS
                factors_applied.append(
                    f"Wallet age bonus (+{self.WALLET_AGE_BONUS * 100:.0f}%): "
                    f"{wallet_age_days:.1f} days > {self.WALLET_AGE_THRESHOLD_DAYS} days"
                )
            
            # Transaction count bonus
            if tx_count > self.TX_COUNT_THRESHOLD:
                total_bonus += self.TX_COUNT_BONUS
                factors_applied.append(
                    f"Transaction activity bonus (+{self.TX_COUNT_BONUS * 100:.0f}%): "
                    f"{tx_count} transactions > {self.TX_COUNT_THRESHOLD}"
                )
            
            # Value moved bonus
            if total_value_moved > self.VALUE_MOVED_THRESHOLD_ETH:
                total_bonus += self.VALUE_MOVED_BONUS
                factors_applied.append(
                    f"Value moved bonus (+{self.VALUE_MOVED_BONUS * 100:.0f}%): "
                    f"{total_value_moved:.2f} ETH > {self.VALUE_MOVED_THRESHOLD_ETH} ETH"
                )
        
        # Cap total bonus
        if total_bonus > self.MAX_TOTAL_BONUS:
            factors_applied.append(f"Bonus capped at {self.MAX_TOTAL_BONUS * 100:.0f}%")
            total_bonus = self.MAX_TOTAL_BONUS
        
        # Calculate final amount
        final_amount = base_amount * (1 + total_bonus)
        # Round to nearest dollar
        final_amount = round(final_amount)
        
        # Cap at max loan amount
        if final_amount > self.max_loan_amount_usdc:
            final_amount = self.max_loan_amount_usdc
            factors_applied.append(f"Amount capped at maximum: ${self.max_loan_amount_usdc:,}")
        
        return {
            "eligible": True,
            "amount_usdc": final_amount,
            "reason": f"Eligible with trust score {trust_score} (tier: {tier_name})",
            "factors_applied": factors_applied
        }
    
    def check_eligibility(
        self,
        trust_score: int,
        wallet_summary: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Check eligibility and calculate eligible loan amount.
        
        This is an alias for calculate_eligible_amount for consistency.
        
        Args:
            trust_score: Trust score (0-100)
            wallet_summary: Optional wallet summary from OnChainVerifier
        
        Returns:
            Dictionary with eligibility status and amount
        """
        return self.calculate_eligible_amount(trust_score, wallet_summary)

