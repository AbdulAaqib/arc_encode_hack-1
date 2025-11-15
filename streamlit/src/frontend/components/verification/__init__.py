"""
Verification module package.

Provides comprehensive verification and scoring functionality for wallet addresses
and user profiles. Includes on-chain analysis, off-chain validation, wallet verification,
score calculation, and eligibility checking.
"""

from .eligibility_checker import EligibilityChecker
from .offchain_verifier import OffChainVerifier
from .onchain_verifier import OnChainVerifier
from .score_calculator import ScoreCalculator
from .wallet_verifier import WalletVerifier
from .verification_flow import run_verification_flow

__all__ = [
    "EligibilityChecker",
    "OffChainVerifier",
    "OnChainVerifier",
    "ScoreCalculator",
    "WalletVerifier",
    "run_verification_flow",
]

