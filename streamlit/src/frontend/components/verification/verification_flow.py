"""
Verification flow orchestrator.

This module provides a complete verification flow that combines:
1. Wallet Verification
2. On-chain Verification
3. Off-chain Verification
4. Score Calculation
5. Eligibility Check

This is a backend module that processes verification data and returns results.
"""

import asyncio
import sys
from typing import Dict, Any, Optional, List

try:
    from .wallet_verifier import WalletVerifier
    from .onchain_verifier import OnChainVerifier
    from .offchain_verifier import OffChainVerifier
    from .score_calculator import ScoreCalculator
    from .eligibility_checker import EligibilityChecker
except ImportError:
    # Fallback for direct execution - set up package structure and import
    import os
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(current_dir)  # components directory
    
    # Add parent directory to path and set up package structure
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
    
    # Create a fake package structure by creating a verification module namespace
    # This allows relative imports in the modules to work
    import types
    verification_pkg = types.ModuleType('verification')
    verification_pkg.__path__ = [current_dir]
    sys.modules['verification'] = verification_pkg
    
    # Now import modules - they'll be able to use relative imports
    # We need to load them in order to handle dependencies
    import importlib.util
    
    def _load_module_as_package(module_name, file_path):
        """Load a module as part of the verification package."""
        full_name = f"verification.{module_name}"
        spec = importlib.util.spec_from_file_location(full_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load module {module_name} from {file_path}")
        module = importlib.util.module_from_spec(spec)
        module.__package__ = "verification"
        sys.modules[full_name] = module
        spec.loader.exec_module(module)
        return module
    
    # Load modules in dependency order
    wallet_verifier_mod = _load_module_as_package("wallet_verifier", os.path.join(current_dir, "wallet_verifier.py"))
    onchain_verifier_mod = _load_module_as_package("onchain_verifier", os.path.join(current_dir, "onchain_verifier.py"))
    offchain_verifier_mod = _load_module_as_package("offchain_verifier", os.path.join(current_dir, "offchain_verifier.py"))
    score_calculator_mod = _load_module_as_package("score_calculator", os.path.join(current_dir, "score_calculator.py"))
    eligibility_checker_mod = _load_module_as_package("eligibility_checker", os.path.join(current_dir, "eligibility_checker.py"))
    
    # Extract classes
    WalletVerifier = wallet_verifier_mod.WalletVerifier
    OnChainVerifier = onchain_verifier_mod.OnChainVerifier
    OffChainVerifier = offchain_verifier_mod.OffChainVerifier
    ScoreCalculator = score_calculator_mod.ScoreCalculator
    EligibilityChecker = eligibility_checker_mod.EligibilityChecker


# Helper context manager for progress messages
class _ProgressContext:
    def __init__(self, message: str):
        self.message = message
    
    def __enter__(self):
        print(f"[{self.message}]")
        return self
    
    def __exit__(self, *args):
        pass


async def run_verification_flow(user_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run the complete verification flow.
    
    Args:
        user_data: Dictionary containing user input data
    
    Returns:
        Dictionary with all verification results
    """
    results = {
        "wallet_verification": None,
        "onchain_verification": None,
        "offchain_verification": None,
        "score_calculation": None,
        "eligibility_check": None,
        "errors": []
    }
    
    wallet_address = user_data.get("wallet_address")
    
    if not wallet_address:
        results["errors"].append("Wallet address is required")
        return results
    
    try:
        # Step 1: Wallet Verification
        with _ProgressContext("🔍 Step 1/5: Verifying wallet format and on-chain activity..."):
            wallet_verifier = WalletVerifier()
            wallet_result = await wallet_verifier.verify_wallet(wallet_address)
            results["wallet_verification"] = wallet_result
            
            if not wallet_result["valid_format"]:
                results["errors"].append(f"Wallet format invalid: {wallet_result['reason']}")
                return results
            
            if not wallet_result["active_onchain"]:
                results["errors"].append(f"Wallet has no on-chain activity: {wallet_result['reason']}")
                return results
        
        # Step 2: On-chain Verification
        with _ProgressContext("⛓️ Step 2/5: Analyzing on-chain wallet data..."):
            onchain_verifier = OnChainVerifier()
            onchain_summary = await onchain_verifier.get_wallet_summary(wallet_address)
            results["onchain_verification"] = onchain_summary
        
        # Step 3: Off-chain Verification
        with _ProgressContext("📋 Step 3/5: Verifying off-chain information..."):
            offchain_verifier = OffChainVerifier()
            offchain_result = offchain_verifier.compute_offchain_score(
                uploaded_files=user_data.get("uploaded_files"),
                email=user_data.get("email"),
                phone=user_data.get("phone"),
                name=user_data.get("full_name"),
                social_link=user_data.get("social_link")
            )
            results["offchain_verification"] = offchain_result
        
        # Step 4: Score Calculation
        with _ProgressContext("📊 Step 4/5: Calculating trust score..."):
            score_calculator = ScoreCalculator()
            user_profile = {
                "uploaded_files": user_data.get("uploaded_files"),
                "email": user_data.get("email"),
                "phone": user_data.get("phone"),
                "name": user_data.get("full_name"),
                "social_link": user_data.get("social_link")
            }
            score_result = await score_calculator.compute_score(wallet_address, user_profile)
            results["score_calculation"] = score_result
        
        # Step 5: Eligibility Check
        with _ProgressContext("✅ Step 5/5: Checking eligibility..."):
            eligibility_checker = EligibilityChecker()
            eligibility_result = eligibility_checker.check_eligibility(
                trust_score=score_result["final_score"],
                wallet_summary=onchain_summary
            )
            results["eligibility_check"] = eligibility_result
            
    except Exception as e:
        results["errors"].append(f"Verification error: {str(e)}")
        print(f"ERROR: An error occurred during verification: {str(e)}")
    
    return results




# -----------------------------------------------------------
# Self-test when running the file directly
# -----------------------------------------------------------
if __name__ == "__main__":
    # Mock file class to simulate uploaded files
    class MockUploadedFile:
        """Mock file object that simulates Streamlit UploadedFile."""
        def __init__(self, mime_type: str, size: int, name: str = "test_file"):
            self.type = mime_type
            self.size = size
            self.name = name
            self._content = b"x" * size  # Create dummy content of specified size
        
        def read(self):
            return self._content
        
        def seek(self, position: int, whence: int = 0):
            pass
        
        def tell(self):
            return self.size
    
    async def test_verification_flow():
        """Test the verification flow with sample data."""
        print("Testing Verification Flow...\n")
        
        # Create mock uploaded files (PDF and PNG, both > 20 KB)
        mock_files = [
            MockUploadedFile("application/pdf", 25 * 1024, "test_document.pdf"),  # 25 KB PDF
            MockUploadedFile("image/png", 30 * 1024, "test_image.png")  # 30 KB PNG
        ]
        
        test_data = {
            "wallet_address": "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
            "full_name": "John Doe",
            "email": "user@gmail.com",
            "phone": "+12345678901",
            "social_link": "https://github.com/johndoe",
            "uploaded_files": mock_files
        }
        
        results = await run_verification_flow(test_data)
        
        print("\n=== Verification Results ===")
        print(f"Wallet Verification: {results.get('wallet_verification')}")
        print(f"On-chain Verification: {results.get('onchain_verification')}")
        print(f"Off-chain Verification: {results.get('offchain_verification')}")
        print(f"Score Calculation: {results.get('score_calculation')}")
        print(f"Eligibility Check: {results.get('eligibility_check')}")
        if results.get("errors"):
            print(f"\nErrors: {results['errors']}")
    
    asyncio.run(test_verification_flow())