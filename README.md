# TrustMint — Dynamic, AI-Powered Credit on Arc

A hackathon MVP that computes a dynamic credit reputation from on-chain + off-chain signals, issues an on-chain credential (SBT-style), and unlocks a USDC credit line on Arc.

---

## Elevator Pitch

TrustMint is an AI-powered credit engine built on Arc. It delivers dynamic financial reputations for digital creators and SMBs who are overlooked by banks or locked out of DeFi. TrustMint analyses both off-chain real-world financial data and on-chain behaviour, mints a non-transferable Soul-Bound Token (SBT)-style credential representing that reputation, and instantly unlocks a USDC-loan against it. By leveraging Arc’s predictable USDC gas-model and enterprise-grade infrastructure, we deliver smooth credit access to underserved users.

---

## The Problem

- Many freelancers, micro-businesses and creators have non-traditional incomes or fragmented records, so banks reject them.
- DeFi platforms usually demand large crypto collateral — unavailable to these users.
- Their financial reputation exists, but is scattered and not leveraged in one place.
- Therefore they are credit-invisible despite real financial health.

---

## The Solution: TrustMint Journey

1) User On-boards via Chat AI
- The user interacts with a friendly AI agent (Streamlit web UI) that guides onboarding and eligibility.

2) On-Chain Reputation
- User enters their Arc wallet address. We compute a Web3 reputation from on-chain metrics (wallet age, activity, stability).

3) Off-Chain Reputation
- User uploads a financial document (e.g., bank statement PDF). An LLM parses net income, cash flow, expenses, consistency.

4) Unified Score Calculation
- AI merges both sources to compute a unified TrustMint Score.

5) Mint the Credential (SBT-style)
- For the MVP, we implement a Score Registry that is issuer-controlled and non-transferable (updatable mapping). In a next step this becomes a formal SBT NFT with metadata.

6) Dynamic Updates
- Reputation changes, so the credential is updatable by the issuer: issueScore overwrites current score; revokeScore marks it invalid.

7) Loan Unlocking
- With a valid score, a CreditLineManager contract determines how much USDC a borrower can draw.

8) Use of Funds
- USDC can be spent across Arc’s stablecoin-native ecosystem (payments, CCTP, off-ramping), giving real working capital utility.

---

## Why It Hits the Hackathon Themes

- Identity-based lending / verifiable credentials: The on-chain score is a verifiable credential read by the lending logic.
- Reputation-driven credit with cash-flow: We combine on-chain behaviour + real-world financial data to underwrite, not just collateral.
- SMB & creator credit: Explicitly targets the underserved.
- Built for Arc: USDC-native gas model, EVM compatibility, sub-second finality, enterprise-grade infrastructure.

---

## Key Architecture & Tech Highlights

- Contracts (Solidity on Arc testnet)
  - CreditScoreRegistry: issuer-only issueScore/revokeScore; view getScore(borrower) returns (value, timestamp, valid).
  - CreditLineManager: creates and manages USDC-denominated credit lines; borrower can draw and repay; view availableCredit.
- Streamlit UI (Python)
  - Intro page (overview), Chatbot (Azure OpenAI + doc parsing), MCP Tools (contract calls: read score/credit, draw, revoke/issue).
- AI + Data Integration
  - On-chain via RPC; off-chain via document upload and LLM parsing (PDF/DOCX supported locally if packages installed).
- Gas Sponsorship (future work)
  - App wallet pays gas; Arc’s USDC gas model enables predictable fees and a smooth UX.
- Security & Compliance (future work)
  - Optional privacy, stablecoin-native environment, least-privileged issuer keys, revocation.

---

## Repository Layout

- blockchain_code/
  - src/CreditScoreRegistry.sol — Updatable, issuer-controlled score mapping (MVP credential).
  - src/CreditLineManager.sol — USDC credit line issuance, draw, repay, and available credit views.
  - out/ — Foundry build artifacts (JSON with ABI under `abi` field).
- streamlit/
  - src/frontend/app.py — Streamlit entrypoint (loads .env from repo root).
  - src/frontend/components/ — Chatbot, MCP Tools, Web3 helpers.

---

## Quickstart

Prereqs
- Python 3.10+ (repo includes a venv with 3.13), pip
- Foundry (forge, cast). Install: `curl -L https://foundry.paradigm.xyz | bash && foundryup`

1) Clone + setup Python deps

```bash
cd /Users/abdulaaqib/Developer/Github/arc_encode_hack
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

2) Create .env at repo root

The Streamlit app auto-loads `.env` from the repo root on startup.

```bash
# Azure OpenAI (for the Chatbot + MCP assistant)
AZURE_OPENAI_ENDPOINT=your_azure_openai_endpoint
AZURE_OPENAI_KEY=your_azure_openai_key
AZURE_OPENAI_API_VERSION=2024-06-01
AZURE_OPENAI_CHAT_DEPLOYMENT=your_deployment_name  # e.g., gpt-4o-mini / gpt-4o

# Arc RPC + signing key (LOCAL DEV ONLY — never commit or share)
ARC_TESTNET_RPC_URL=https://arc-testnet.example.rpc  # replace with actual Arc testnet RPC
PRIVATE_KEY=0xabc123...  # use a test-only key; sufficient testnet USDC balance is required to draw/repay

# Contract addresses (use deployed testnet addresses)
CREDIT_SCORE_REGISTRY_ADDRESS=0xYourRegistryAddress
# Optional (enables draw/repay panel in MCP Tools):
CREDIT_LINE_MANAGER_ADDRESS=0xYourCreditLineManager

# ABI paths (point to Foundry build artifacts)
# Use the Registry ABI for score tools or the Manager ABI for credit tools.
ARC_CREDIT_LINE_MANAGER_ABI_PATH=blockchain_code/out/CreditScoreRegistry.sol/CreditScoreRegistry.json
# Alternatively:
# ARC_CREDIT_LINE_MANAGER_ABI_PATH=blockchain_code/out/CreditLineManager.sol/CreditLineManager.json

# Tuning (optional)
ARC_USDC_DECIMALS=6
ARC_GAS_LIMIT=200000
ARC_GAS_PRICE_GWEI=1
CHATBOT_ATTACHMENT_MAX_CHARS=6000
CHAT_PREVIEW_MAX_CHARS=1000
```

3) Build and (optionally) deploy contracts with Foundry

```bash
cd blockchain_code
forge build
# run tests
forge test -vv

# deploy CreditScoreRegistry (constructor: initialOwner)
forge create src/CreditScoreRegistry.sol:CreditScoreRegistry \
  --rpc-url "$ARC_TESTNET_RPC_URL" \
  --private-key "$PRIVATE_KEY" \
  --constructor-args 0xYourOwnerAddress

# deploy CreditLineManager (constructor: IERC20 stablecoin, initialOwner)
# Use the Arc testnet USDC address for the first argument
forge create src/CreditLineManager.sol:CreditLineManager \
  --rpc-url "$ARC_TESTNET_RPC_URL" \
  --private-key "$PRIVATE_KEY" \
  --constructor-args 0xArcTestnetUSDC 0xYourOwnerAddress
```

Copy the deployed addresses into `.env` as `CREDIT_SCORE_REGISTRY_ADDRESS` and, if using the draw/repay features, `CREDIT_LINE_MANAGER_ADDRESS`.

4) Run the Streamlit app

```bash
# From repo root (ensure your .env is in the repo root)
source venv/bin/activate
streamlit run streamlit/src/frontend/app.py
```

Navigate through the sidebar:
- Intro — project overview and setup reminders
- Chatbot — Azure OpenAI-powered assistant; can include document uploads; will guide you to configure env vars
- MCP Tools — interactive panel for reading score/credit, and sending transactions (if PRIVATE_KEY is set)

---

## Demo Flow (MVP)

- Enter a wallet address in Tools or ask the Chatbot to inspect it → reads `getScore` and `availableCredit`.
- Issue a score for a borrower (issuer-only, via Tools or LLM tool) → `issueScore(borrower, value)` records value/timestamp and sets valid=true.
- Revoke a score (issuer-only) → `revokeScore(borrower)` sets valid=false.
- Create a credit line (owner-only) → `createCreditLine(borrower, limit, interestBps)`.
- Borrower draws funds → `draw(borrower, amount)` transfers USDC to borrower if within limit.
- Borrower repays → `repay(borrower, amount)`.
- Observe that an increased score (issuer update) can be reflected in UI and lending terms.

---

## Business Model (Concept)

- Underwriting fee or small interest spread.
- Tiered services (higher scores → larger limits, lower rates).
- Partnerships with SMB/creator tooling (accounting, payments) for funnel + data integrations.
- Optional aggregated insights (with permission, anonymized) for lenders/insurers.

---

## Roadmap

- Formal SBT implementation (non-transferable ERC-721 with metadata pointing to score and proofs).
- Gas sponsorship for mint/update flows.
- Robust score model: merge on-chain analytics + off-chain bank data, invoices, platform revenue.
- Risk management, interest accrual, late fees, liquidations.
- Lender UI and third-party verifier interface using the SBT credential.

---

## Notes & Disclaimers

- This repository is for hackathon/demo use on testnets. Do not use real keys or funds.
- PRIVATE_KEY must remain private; recommended to use a dedicated test wallet with minimal funds.
- RPC endpoints and USDC addresses differ per network; replace placeholders with actual Arc testnet values.

---

## Want a 2-slide deck?

Happy to generate:
- Slide 1 — Problem + Solution (TrustMint Journey)
- Slide 2 — Architecture + Business + Ask

Open an issue or ping in chat to export a tailored deck outline.
