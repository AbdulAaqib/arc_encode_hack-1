# TrustMint — Identity‑based Lending on Arc (SBT + Pool MVP) hello

A credit‑infrastructure MVP on Arc that lets underserved creators and SMBs access stable‑coin loans using a verifiable on‑chain credential and a unified credit score from on‑chain + off‑chain data.

---

## What it does

- Builds an identity‑based lending flow on Arc using a non‑transferable Soul‑Bound Token (SBT) as a verifiable credential.
- Computes a TrustMint Score by merging on‑chain reputation (wallet history, activity) with off‑chain cash‑flow signals (uploaded docs).
- Unlocks USDC working‑capital loans for eligible borrowers.
- MVP ships SBT credential + credit line manager. Lender deposits/withdrawals are planned next (see Roadmap).

---

## How it works (end‑to‑end)

1) Wallet Connection & Verification
- User connects a wallet in the Streamlit app and (optionally) signs a message for ownership.

2) Off‑Chain + On‑Chain Data Collection
- On‑chain: fetch wallet metrics (wallet age, transaction activity/volume, behavior).
- Off‑chain: user uploads bank statements or provides simplified income/expenses; system extracts net income, consistency, spend patterns.
- A unified TrustMint Score is computed from both sources.

3) Credential Issuance (SBT)
- If the user meets criteria, the smart contract mints a non‑transferable SBT to their wallet representing their credit identity and current score.

4) Lender Pool & Deposits (Planned in next iteration)
- Lenders deposit USDC into a Lending Pool contract and receive a representation of their share (e.g., ERC‑4626‑style share token). Liquidity is used for borrower draws; lenders can later withdraw their share plus any returns.
- For the MVP, you can seed liquidity directly into the CreditLineManager contract (send testnet USDC) to allow draws.

5) Borrower Loan Draw
- Borrower sees eligibility (e.g., "You’re eligible for X USDC").
- Press "Draw"; contract verifies eligibility (SBT/score gating planned on‑chain; currently enforced off‑chain by issuer/governance who creates lines for eligible users) and available liquidity. On success, USDC is transferred to borrower.

6) Borrower Repayment
- Borrower repays (principal, or principal+return if enabled) and contract updates their outstanding balance/status.

7) Lender Withdrawal & Returns (Planned)
- As borrowers repay, the pool is replenished. Lenders can redeem their share for underlying USDC and any accrued return.

8) Arc‑specific advantages
- USDC‑native fees and predictable costs, making working‑capital lending practical. Sub‑second finality and EVM‑compatibility.

---

## Why this matters

- Identity‑based lending: The SBT acts as a verifiable on‑chain credential for credit identity.
- Reputation‑driven credit: Combines on‑chain behavior with off‑chain cash‑flow, not pure crypto collateral.
- Stable‑coin native: Loans are USDC‑denominated on Arc.
- Lender/borrower market: Funds sourced from lenders; borrowers draw and repay; creates a full credit loop (pool planned in next iteration).

---

## Key Architecture & Contracts

- TrustMintSBT.sol (deployed in MVP)
  - Non‑transferable ERC‑721 (ERC‑5192 semantics); one token per wallet.
  - Functions: `issueScore(borrower, value)`, `revokeScore(borrower)`, `getScore(borrower) -> (value, timestamp, valid)`, `hasSbt(wallet)`, `tokenIdOf(wallet)`.
  - Metadata via `tokenURI`; transfer/burn disabled; owner is the issuer.

- CreditLineManager.sol (deployed in MVP)
  - Owner‑managed USDC credit lines with `limit`, `drawn`, `interestRate` (bps), and `availableCredit` view.
  - `draw(borrower, amount)` transfers USDC held by the contract; `repay(borrower, amount)` returns USDC to the contract.
  - Note: For MVP, seed this contract with testnet USDC so draws succeed. Lender deposit/withdraw flows are planned in the pool contract.

- CreditScoreRegistry.sol (optional alternative)
  - Minimal issuer‑only registry maintaining an updatable score mapping. Kept for compatibility and comparison with the SBT approach.

- Lending Pool (planned)
  - ERC‑4626‑style pool with deposits/withdrawals, LP tokens, and on‑chain verification of borrower credential and score.

---

## Repository Layout

- `blockchain_code/`
  - `src/TrustMintSBT.sol` — SBT credential with score binding.
  - `src/CreditLineManager.sol` — Credit lines: create, draw, repay, close, and `availableCredit`.
  - `src/CreditScoreRegistry.sol` — Optional minimal registry.
  - `out/` — Foundry build artifacts (ABIs under the `abi` field of each JSON).
- `streamlit/`
  - `src/frontend/app.py` — Streamlit entrypoint (auto‑loads `.env` at repo root).
  - `src/frontend/components/` — Chatbot, MCP Tools (SBT‑focused), wallet connect, and helpers.

---

## Quickstart

Prereqs
- Python 3.12
- Foundry (forge, cast). Install: `curl -L https://foundry.paradigm.xyz | bash && foundryup`

1) Clone + setup Python deps

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2) Create `.env` at repo root

The Streamlit app auto‑loads `.env` from the repo root.

```bash
# Azure OpenAI (Chatbot + parsing)
AZURE_OPENAI_ENDPOINT=your_azure_openai_endpoint
AZURE_OPENAI_KEY=your_azure_openai_key
AZURE_OPENAI_API_VERSION=2024-06-01
AZURE_OPENAI_CHAT_DEPLOYMENT=your_deployment_name  # e.g., gpt-4o-mini / gpt-4o

# Arc RPC + signing key (LOCAL DEV ONLY — never commit or share)
ARC_TESTNET_RPC_URL=https://rpc.testnet.arc.network  # Arc Testnet RPC
PRIVATE_KEY=0xabc123...  # test-only key with minimal funds

# SBT contract (used by the MCP Tools UI)
SBT_ADDRESS=0xYourDeployedSbt
TRUSTMINT_SBT_ABI_PATH=blockchain_code/out/TrustMintSBT.sol/TrustMintSBT.json

# Optional gas tuning
ARC_USDC_DECIMALS=6
ARC_GAS_LIMIT=200000
ARC_GAS_PRICE_GWEI=1

# Optional advanced (CLI only for now)
# CREDIT_LINE_MANAGER_ADDRESS=0xYourCreditLineManager
# CREDIT_LINE_MANAGER_ABI_PATH=blockchain_code/out/CreditLineManager.sol/CreditLineManager.json
```

3) Build and (optionally) deploy contracts with Foundry

```bash
cd blockchain_code
forge build
# run tests
forge test -vv

# Deploy SBT (constructor: name, symbol, initialOwner)
forge create src/TrustMintSBT.sol:TrustMintSBT \
  --rpc-url "$ARC_TESTNET_RPC_URL" \
  --private-key "$PRIVATE_KEY" \
  --constructor-args "TrustMint SBT" TMSBT 0xYourOwnerAddress

# Optional: Deploy CreditLineManager (constructor: IERC20 stablecoin, initialOwner)
# Use Arc testnet USDC address for the first argument, then set CREDIT_LINE_MANAGER_ADDRESS in .env
forge create src/CreditLineManager.sol:CreditLineManager \
  --rpc-url "$ARC_TESTNET_RPC_URL" \
  --private-key "$PRIVATE_KEY" \
  --constructor-args 0xArcTestnetUSDC 0xYourOwnerAddress
```

Copy the deployed addresses into `.env` (`SBT_ADDRESS`, optionally `CREDIT_LINE_MANAGER_ADDRESS`).

4) Interact via CLI (SBT)

```bash
# Read score + SBT
cast call $SBT_ADDRESS "hasSbt(address)(bool)" 0xSomeWallet --rpc-url $ARC_TESTNET_RPC_URL
cast call $SBT_ADDRESS "getScore(address)(uint256,uint256,bool)" 0xSomeWallet --rpc-url $ARC_TESTNET_RPC_URL

# Issue / revoke (owner only)
cast send $SBT_ADDRESS "issueScore(address,uint256)" 0xSomeWallet 720 \
  --rpc-url $ARC_TESTNET_RPC_URL --private-key $PRIVATE_KEY
cast send $SBT_ADDRESS "revokeScore(address)" 0xSomeWallet \
  --rpc-url $ARC_TESTNET_RPC_URL --private-key $PRIVATE_KEY
```

5) Run the Streamlit app

```bash
# From repo root (ensure your .env is in the repo root)
source venv/bin/activate
streamlit run streamlit/src/frontend/app.py
```

Navigate via the sidebar:
- Intro — project overview and setup reminders
- Chatbot — Azure OpenAI‑powered assistant with doc uploads for off‑chain parsing
- MCP Tools — interactive panel for SBT calls: hasSbt, getScore, issueScore, revokeScore

### Owner USDC Tools (Same-Chain & CCTP)

- Configure `ARC_TESTNET_RPC_URL`, `LENDING_POOL_ADDRESS`, and either `ARC_OWNER_PRIVATE_KEY` or `PRIVATE_KEY` in `.env`.
- In the Streamlit "Wallet Connect" or "MCP Tools" pages you get two distinct flows:
  - **ARC → ARC** — calls `transferUsdcOnArc` so the lending pool owner can pay any ARC wallet directly (no CCTP involved).
  - **ARC → Polygon (CCTP)** — calls `prepareCctpBridge` to move USDC from the pool into the owner wallet, then the app signs the Circle Token Messenger `depositForBurn` so the funds can mint on Polygon (or other supported chains) after attestation.
- The UI surfaces three ARC transactions (prepare, optional allowance approval, burn) plus the Polygon mint payload. If you set `POLYGON_RPC` and `POLYGON_PRIVATE_KEY`, the app will automatically submit the Polygon `receiveMessage` call; otherwise, it exposes the message & attestation along with a MetaMask “Mint on Polygon” button so you can send the transaction manually.
- Polygon minting (automatic or manual) still requires the Polygon signer to hold test MATIC for gas.

---

## Demo Flow (MVP)

- Connect a wallet and check eligibility via the UI/CLI.
- Issue a score for a borrower (issuer‑only) → `issueScore(borrower, value)` stores value/timestamp, sets valid=true, and mints SBT if missing.
- Revoke a score (issuer‑only) → `revokeScore(borrower)` sets valid=false; SBT remains non‑transferable and bound.
- Optional (CLI): Create a credit line (owner‑only), seed the CreditLineManager with testnet USDC, then draw/repay.
  - Create: `createCreditLine(borrower, limit, interestBps)`
  - Draw: `draw(borrower, amount)` (transfers USDC held by the contract)
  - Repay: `repay(borrower, amount)` (requires ERC20 allowance)

---

## Design: Lender Pool & Returns (Planned)

- Deposits: Lenders deposit USDC into a dedicated pool contract and receive a share token (likely ERC‑4626) representing their portion of the liquidity.
- Draws: Borrowers meeting SBT/score criteria draw from the pool subject to utilization and policy.
- Repayment: Principal (and optionally interest/fees) replenishes the pool.
- Withdrawals: Lenders redeem their shares for underlying USDC and any accrued return.
- Transparency: On‑chain metrics reveal utilization, borrower behavior, and pool health.

For the MVP, pool functions are not yet implemented on‑chain. Seed liquidity directly to `CreditLineManager` to enable draws.

---

## Arc‑specific notes

- USDC‑native gas model enables predictable fees and smooth UX.
- EVM‑compatible, sub‑second finality; easy integration with wallets and tooling.
- The Arc testnet RPC endpoint is https://rpc.testnet.arc.network (Chain ID: 5042002)

---

## Business Model (Concept)

- Underwriting fee or a small interest spread.
- Tiered services (higher scores → larger limits, lower rates).
- Partnerships with SMB/creator tools for distribution and richer data.
- Optional aggregated, privacy‑preserving insights for lenders/insurers.

---

## Roadmap

- Implement Lending Pool: deposits/withdrawals with ERC‑4626 shares; on‑chain checks for SBT + score; liquidity accounting and return distribution.
- Gas sponsorship for mint/update flows; UX polish.
- Score model hardening: merge deeper on‑chain analytics + off‑chain bank data, invoices, platform revenue.
- Full risk management: interest accrual, late fees, delinquency handling.
- Lender dashboard and third‑party verifier interface using the SBT credential.

---

## Notes & Disclaimers

- This repository is for hackathon/demo use on testnets. Do not use real keys or funds.
- Use a dedicated test wallet with minimal funds for `PRIVATE_KEY`.
- Arc testnet uses USDC as native currency. Network details: RPC: https://rpc.testnet.arc.network, Chain ID: 5042002, Explorer: https://testnet.arcscan.app
