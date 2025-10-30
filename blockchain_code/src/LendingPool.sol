// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {SafeCast} from "@openzeppelin/contracts/utils/math/SafeCast.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/// @notice Minimal interface for TrustMintSBT used for gating
interface ITrustMintSBT {
    function hasSbt(address wallet) external view returns (bool);
    function getScore(address wallet) external view returns (uint256 value, uint256 timestamp, bool valid);
}

/**
 * @title LendingPool
 * @notice Accepts native-token deposits (USDC-denominated on Arc), issues loans, and enforces timed lender withdrawals.
 *         Funds are transferred into the pool contract immediately; borrowers repay in native token as well.
 */
contract LendingPool is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    enum LoanState {
        None,
        Active,
        Repaid,
        Defaulted
    }

    // --- Action identifiers ---
    string private constant ACTION_DEPOSIT = "DEPOSIT";
    string private constant ACTION_WITHDRAW = "WITHDRAW";
    string private constant ACTION_OPEN_LOAN = "OPEN_LOAN";
    string private constant ACTION_REPAY = "REPAY";
    string private constant ACTION_CHECK_DEFAULT = "CHECK_DEFAULT";
    string private constant ACTION_UNBAN = "UNBAN";

    // --- Reason codes ---
    string private constant REASON_OK = "OK";
    string private constant REASON_AMOUNT_ZERO = "AMOUNT_ZERO";
    string private constant REASON_VALUE_MISMATCH = "VALUE_MISMATCH";
    string private constant REASON_AMOUNT_TOO_LARGE = "AMOUNT_TOO_LARGE";
    string private constant REASON_WITHDRAW_EXCEEDS = "WITHDRAW_EXCEEDS_DEPOSITS";
    string private constant REASON_ENTRY_DEPLETED = "DEPOSIT_ENTRY_DEPLETED";
    string private constant REASON_WITHDRAW_LOCKED = "WITHDRAW_LOCKED";
    string private constant REASON_LIQUIDITY = "INSUFFICIENT_POOL_LIQUIDITY";
    string private constant REASON_TRANSFER_FAILED = "NATIVE_TRANSFER_FAILED";
    string private constant REASON_BORROWER_BANNED = "BORROWER_BANNED";
    string private constant REASON_PRINCIPAL_ZERO = "PRINCIPAL_ZERO";
    string private constant REASON_TERM_ZERO = "TERM_ZERO";
    string private constant REASON_NO_SBT = "MISSING_SBT";
    string private constant REASON_SCORE_INVALID = "SCORE_INVALID";
    string private constant REASON_SCORE_LOW = "SCORE_TOO_LOW";
    string private constant REASON_ACTIVE_LOAN = "ACTIVE_LOAN_PRESENT";
    string private constant REASON_NO_ACTIVE_LOAN = "NO_ACTIVE_LOAN";
    string private constant REASON_REPAY_MISMATCH = "REPAY_AMOUNT_MISMATCH";
    string private constant REASON_REPAY_TOO_LARGE = "REPAY_AMOUNT_TOO_LARGE";
    string private constant REASON_REPAID = "LOAN_REPAID";
    string private constant REASON_NOT_OVERDUE = "NOT_OVERDUE";
    string private constant REASON_ALREADY_BANNED = "ALREADY_BANNED";
    string private constant REASON_NOT_BANNED = "NOT_BANNED";
    string private constant REASON_LOAN_DEFAULTED = "LOAN_DEFAULTED";
    string private constant REASON_USDC_AMOUNT_ZERO = "USDC_AMOUNT_ZERO";
    string private constant REASON_RECIPIENT_ZERO = "RECIPIENT_ZERO";
    string private constant REASON_USDC_BALANCE = "USDC_BALANCE";
    string private constant REASON_OWNER_ZERO = "OWNER_ZERO";

    // --- Custom errors ---
    error PoolActionRejected(string reason);
    error LoanActionRejected(string reason);
    error GovernanceActionRejected(string reason);
    error BridgeActionRejected(string reason);

    // --- Events ---
    event PoolActionEvaluated(
        string action,
        address indexed account,
        bool success,
        string reason,
        uint256 amount,
        uint256 poolBalance,
        uint256 lenderUnlockable
    );

    event LoanActionEvaluated(
        string action,
        address indexed borrower,
        bool success,
        string reason,
        LoanState state,
        uint256 principal,
        uint256 outstanding,
        uint256 dueTime,
        bool banned
    );

    event Deposited(address indexed lender, uint256 amount, uint256 timestamp);
    event Withdrawn(address indexed lender, uint256 amount);
    event LoanOpened(address indexed borrower, uint256 principal, uint256 startTime, uint256 dueTime);
    event LoanRepaid(address indexed borrower, uint256 amount, uint256 remaining);
    event BorrowerBanned(address indexed borrower);
    event BorrowerUnbanned(address indexed borrower);
    event ArcUsdcTransferred(address indexed recipient, uint256 amount);
    event CctpBridgePrepared(address indexed ownerWallet, uint256 amount);

    // --- Optional credential gating ---
    address public constant TRUST_MINT_SBT = 0xc570aBb715c7E51801AE7dea5B08Af53c6718BAD;
    uint256 public constant MIN_SCORE_TO_BORROW = 400;

    // --- Circle CCTP constants (ARC Testnet → Polygon Amoy) ---
    address public constant ARC_USDC_ADDRESS = 0x3600000000000000000000000000000000000000;
    address public constant CCTP_TOKEN_MESSENGER = 0x8FE6B999Dc680CcFDD5Bf7EB0974218be2542DAA;
    uint32 public constant CCTP_POLYGON_DOMAIN = 7;
    // Use Circle's per-chain default finality (0) so attestations become available quickly.
    uint32 public constant CCTP_DEFAULT_MIN_FINALITY = 0;

    // --- Lender accounting ---
    struct DepositEntry {
        uint128 amount;
        uint64 timestamp;
    }

    mapping(address => DepositEntry[]) private _deposits;
    mapping(address => uint256) public nextWithdrawalIndex;
    mapping(address => uint256) public totalDeposited;
    mapping(address => uint256) public totalWithdrawn;
    uint256 public totalDeposits;

    uint256 public constant DEPOSIT_LOCK_SECONDS = 1;

    // --- Borrower loan state ---
    struct Loan {
        uint256 principal;
        uint256 outstanding;
        uint256 startTime;
        uint256 dueTime;
        LoanState state;
    }
    mapping(address => Loan) public loans;

    // --- Ban list ---
    mapping(address => bool) public banned;

    constructor(address initialOwner) Ownable(initialOwner) {}

    // --- Configuration ---
    function trustMintSbt() public pure returns (address) {
        return TRUST_MINT_SBT;
    }

    function minScoreToBorrow() public pure returns (uint256) {
        return MIN_SCORE_TO_BORROW;
    }

    function depositLockSeconds() public pure returns (uint256) {
        return DEPOSIT_LOCK_SECONDS;
    }

    // --- Internal helpers ---
    function _emitPoolEvaluation(
        string memory action,
        address account,
        bool success,
        string memory reason,
        uint256 amount
    ) internal {
        uint256 poolBalance = address(this).balance;
        uint256 unlockable = previewWithdraw(account);
        emit PoolActionEvaluated(action, account, success, reason, amount, poolBalance, unlockable);
    }

    function _failPoolAction(
        string memory action,
        address account,
        string memory reason,
        uint256 amount
    ) internal {
        _emitPoolEvaluation(action, account, false, reason, amount);
        revert PoolActionRejected(reason);
    }

    function _emitLoanEvaluation(
        string memory action,
        address borrower,
        bool success,
        string memory reason
    ) internal {
        Loan memory snapshot = loans[borrower];
        emit LoanActionEvaluated(
            action,
            borrower,
            success,
            reason,
            snapshot.state,
            snapshot.principal,
            snapshot.outstanding,
            snapshot.dueTime,
            banned[borrower]
        );
    }

    function _failLoanAction(
        string memory action,
        address borrower,
        string memory reason
    ) internal {
        _emitLoanEvaluation(action, borrower, false, reason);
        revert LoanActionRejected(reason);
    }

    function _addressToBytes32(address account) internal pure returns (bytes32) {
        return bytes32(uint256(uint160(account)));
    }

    function _evaluateLoanRequest(
        address borrower,
        uint256 principal,
        uint256 termSeconds
    ) internal view returns (bool, string memory) {
        if (principal == 0) return (false, REASON_PRINCIPAL_ZERO);
        if (termSeconds == 0) return (false, REASON_TERM_ZERO);
        if (banned[borrower]) return (false, REASON_BORROWER_BANNED);
        if (address(this).balance < principal) return (false, REASON_LIQUIDITY);

        if (TRUST_MINT_SBT != address(0)) {
            ITrustMintSBT sbt = ITrustMintSBT(TRUST_MINT_SBT);
            if (!sbt.hasSbt(borrower)) return (false, REASON_NO_SBT);
            (uint256 score,, bool valid) = sbt.getScore(borrower);
            if (!valid) return (false, REASON_SCORE_INVALID);
            if (score < MIN_SCORE_TO_BORROW) return (false, REASON_SCORE_LOW);
        }

        Loan storage existing = loans[borrower];
        if (existing.state == LoanState.Active && existing.outstanding != 0) {
            return (false, REASON_ACTIVE_LOAN);
        }

        return (true, REASON_OK);
    }

    // --- Lender actions ---
    function deposit(uint256 amount) external payable nonReentrant {
        if (amount == 0) {
            _failPoolAction(ACTION_DEPOSIT, msg.sender, REASON_AMOUNT_ZERO, amount);
        }
        if (msg.value != amount) {
            _failPoolAction(ACTION_DEPOSIT, msg.sender, REASON_VALUE_MISMATCH, amount);
        }
        if (amount > type(uint128).max) {
            _failPoolAction(ACTION_DEPOSIT, msg.sender, REASON_AMOUNT_TOO_LARGE, amount);
        }

        uint128 amount128 = SafeCast.toUint128(amount);
        uint64 timestamp64 = SafeCast.toUint64(block.timestamp);

        _deposits[msg.sender].push(DepositEntry({amount: amount128, timestamp: timestamp64}));
        totalDeposited[msg.sender] += amount;
        totalDeposits += amount;

        emit Deposited(msg.sender, amount, block.timestamp);
        _emitPoolEvaluation(ACTION_DEPOSIT, msg.sender, true, REASON_OK, amount);
    }

    function withdraw(uint256 amount) external nonReentrant {
        if (amount == 0) {
            _failPoolAction(ACTION_WITHDRAW, msg.sender, REASON_AMOUNT_ZERO, amount);
        }
        uint256 remaining = amount;
        uint256 idx = nextWithdrawalIndex[msg.sender];
        DepositEntry[] storage entries = _deposits[msg.sender];

        while (remaining > 0) {
            if (idx >= entries.length) {
                _failPoolAction(ACTION_WITHDRAW, msg.sender, REASON_WITHDRAW_EXCEEDS, amount);
            }
            DepositEntry storage entry = entries[idx];
            if (entry.amount == 0) {
                _failPoolAction(ACTION_WITHDRAW, msg.sender, REASON_ENTRY_DEPLETED, amount);
            }
            if (block.timestamp < entry.timestamp + DEPOSIT_LOCK_SECONDS) {
                _failPoolAction(ACTION_WITHDRAW, msg.sender, REASON_WITHDRAW_LOCKED, amount);
            }

            uint256 entryAmount = entry.amount;
            if (entryAmount > remaining) {
                entry.amount = SafeCast.toUint128(entryAmount - remaining);
                remaining = 0;
            } else {
                remaining -= entryAmount;
                entry.amount = 0;
                idx++;
            }
        }

        nextWithdrawalIndex[msg.sender] = idx;
        totalWithdrawn[msg.sender] += amount;
        totalDeposits -= amount;

        uint256 available = address(this).balance;
        if (available < amount) {
            _failPoolAction(ACTION_WITHDRAW, msg.sender, REASON_LIQUIDITY, amount);
        }
        (bool sent, ) = msg.sender.call{value: amount}("");
        if (!sent) {
            _failPoolAction(ACTION_WITHDRAW, msg.sender, REASON_TRANSFER_FAILED, amount);
        }

        emit Withdrawn(msg.sender, amount);
        _emitPoolEvaluation(ACTION_WITHDRAW, msg.sender, true, REASON_OK, amount);
    }

    // --- Borrower actions ---
    function openLoan(address borrower, uint256 principal, uint256 termSeconds) external onlyOwner nonReentrant {
        (bool ok, string memory reason) = _evaluateLoanRequest(borrower, principal, termSeconds);
        if (!ok) {
            _failLoanAction(ACTION_OPEN_LOAN, borrower, reason);
        }

        Loan storage loan = loans[borrower];
        loan.principal = principal;
        loan.outstanding = principal;
        loan.startTime = block.timestamp;
        loan.dueTime = block.timestamp + termSeconds;
        loan.state = LoanState.Active;

        (bool sent, ) = payable(borrower).call{value: principal}("");
        if (!sent) {
            loan.principal = 0;
            loan.outstanding = 0;
            loan.startTime = 0;
            loan.dueTime = 0;
            loan.state = LoanState.None;
            _failLoanAction(ACTION_OPEN_LOAN, borrower, REASON_TRANSFER_FAILED);
        }

        emit LoanOpened(borrower, principal, loan.startTime, loan.dueTime);
        _emitLoanEvaluation(ACTION_OPEN_LOAN, borrower, true, REASON_OK);
    }

    function repay(uint256 amount) external payable nonReentrant {
        Loan storage loan = loans[msg.sender];
        if (loan.state != LoanState.Active) {
            _failLoanAction(ACTION_REPAY, msg.sender, REASON_NO_ACTIVE_LOAN);
        }
        if (amount == 0) {
            _failLoanAction(ACTION_REPAY, msg.sender, REASON_AMOUNT_ZERO);
        }
        if (msg.value != amount) {
            _failLoanAction(ACTION_REPAY, msg.sender, REASON_VALUE_MISMATCH);
        }
        if (amount > loan.outstanding) {
            _failLoanAction(ACTION_REPAY, msg.sender, REASON_REPAY_TOO_LARGE);
        }
        if (amount != loan.outstanding) {
            _failLoanAction(ACTION_REPAY, msg.sender, REASON_REPAY_MISMATCH);
        }

        loan.outstanding = 0;
        loan.state = LoanState.Repaid;

        emit LoanRepaid(msg.sender, amount, 0);
        _emitLoanEvaluation(ACTION_REPAY, msg.sender, true, REASON_REPAID);
    }

    // --- Ban management ---
    function checkDefaultAndBan(address borrower) external {
        Loan storage loan = loans[borrower];
        if (loan.state != LoanState.Active) {
            _emitLoanEvaluation(ACTION_CHECK_DEFAULT, borrower, false, REASON_NO_ACTIVE_LOAN);
            return;
        }
        if (block.timestamp <= loan.dueTime) {
            _emitLoanEvaluation(ACTION_CHECK_DEFAULT, borrower, false, REASON_NOT_OVERDUE);
            return;
        }
        if (loan.outstanding == 0) {
            loan.state = LoanState.Repaid;
            _emitLoanEvaluation(ACTION_CHECK_DEFAULT, borrower, false, REASON_REPAID);
            return;
        }

        if (!banned[borrower]) {
            banned[borrower] = true;
            loan.state = LoanState.Defaulted;
            emit BorrowerBanned(borrower);
            _emitLoanEvaluation(ACTION_CHECK_DEFAULT, borrower, true, REASON_LOAN_DEFAULTED);
        } else {
            _emitLoanEvaluation(ACTION_CHECK_DEFAULT, borrower, false, REASON_ALREADY_BANNED);
        }
    }

    function unban(address borrower) external onlyOwner {
        if (!banned[borrower]) {
            _emitLoanEvaluation(ACTION_UNBAN, borrower, false, REASON_NOT_BANNED);
            revert GovernanceActionRejected(REASON_NOT_BANNED);
        }
        banned[borrower] = false;
        emit BorrowerUnbanned(borrower);
        _emitLoanEvaluation(ACTION_UNBAN, borrower, true, REASON_OK);
    }

    // --- Circle CCTP + ARC transfers ---
    function transferUsdcOnArc(address arcRecipient, uint256 amount) external onlyOwner nonReentrant {
        if (arcRecipient == address(0)) {
            revert BridgeActionRejected(REASON_RECIPIENT_ZERO);
        }
        if (amount == 0) {
            revert BridgeActionRejected(REASON_USDC_AMOUNT_ZERO);
        }

        IERC20 usdc = IERC20(ARC_USDC_ADDRESS);
        if (usdc.balanceOf(address(this)) < amount) {
            revert BridgeActionRejected(REASON_USDC_BALANCE);
        }

        usdc.safeTransfer(arcRecipient, amount);
        emit ArcUsdcTransferred(arcRecipient, amount);
    }

    function prepareCctpBridge(uint256 amount) external onlyOwner nonReentrant returns (address ownerWallet) {
        if (amount == 0) {
            revert BridgeActionRejected(REASON_USDC_AMOUNT_ZERO);
        }
        ownerWallet = owner();
        if (ownerWallet == address(0)) {
            revert BridgeActionRejected(REASON_OWNER_ZERO);
        }

        IERC20 usdc = IERC20(ARC_USDC_ADDRESS);
        if (usdc.balanceOf(address(this)) < amount) {
            revert BridgeActionRejected(REASON_USDC_BALANCE);
        }

        usdc.safeTransfer(ownerWallet, amount);
        emit CctpBridgePrepared(ownerWallet, amount);
    }

    // --- Views ---
    function isBanned(address borrower) external view returns (bool) {
        return banned[borrower];
    }

    function getLoan(address borrower) external view returns (Loan memory) {
        return loans[borrower];
    }

    function lenderBalance(address lender) external view returns (uint256) {
        return totalDeposited[lender] - totalWithdrawn[lender];
    }

    function previewWithdraw(address lender) public view returns (uint256 unlockable) {
        DepositEntry[] storage entries = _deposits[lender];
        uint256 idx = nextWithdrawalIndex[lender];
        uint256 len = entries.length;
        uint256 current = block.timestamp;
        uint256 lockPeriod = DEPOSIT_LOCK_SECONDS;

        while (idx < len) {
            DepositEntry storage entry = entries[idx];
            if (entry.amount == 0) {
                idx++;
                continue;
            }
            if (current < entry.timestamp + lockPeriod) {
                break;
            }
            unlockable += entry.amount;
            idx++;
        }
    }

    function getDeposits(address lender) external view returns (DepositEntry[] memory) {
        return _deposits[lender];
    }

    function loanStatus(address borrower)
        external
        view
        returns (
            LoanState state,
            uint256 principal,
            uint256 outstanding,
            uint256 startTime,
            uint256 dueTime,
            bool bannedStatus
        )
    {
        Loan storage loan = loans[borrower];
        return (loan.state, loan.principal, loan.outstanding, loan.startTime, loan.dueTime, banned[borrower]);
    }

    function lenderStatus(address lender)
        external
        view
        returns (
            uint256 totalDeposited_,
            uint256 totalWithdrawn_,
            uint256 balance,
            uint256 unlockable
        )
    {
        totalDeposited_ = totalDeposited[lender];
        totalWithdrawn_ = totalWithdrawn[lender];
        balance = totalDeposited_ - totalWithdrawn_;
        unlockable = previewWithdraw(lender);
    }

    function canOpenLoan(address borrower, uint256 principal) external view returns (bool ok, string memory reason) {
        return _evaluateLoanRequest(borrower, principal, 1);
    }

    function availableLiquidity() public view returns (uint256) {
        return address(this).balance;
    }
}
