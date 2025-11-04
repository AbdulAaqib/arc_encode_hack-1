// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {SafeCast} from "@openzeppelin/contracts/utils/math/SafeCast.sol";

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
    // --- Optional credential gating ---
    address public trustMintSbt; // set to 0x0 to disable SBT/score checks
    uint256 public minScoreToBorrow = 600;

    // --- Lender accounting ---
    struct DepositEntry {
        uint128 amount;     // remaining amount in this entry (wei)
        uint64 timestamp;   // deposit time
    }

    mapping(address => DepositEntry[]) private _deposits;
    mapping(address => uint256) public nextWithdrawalIndex; // first entry with remaining balance
    mapping(address => uint256) public totalDeposited;
    mapping(address => uint256) public totalWithdrawn;
    uint256 public totalDeposits; // total amount ever deposited minus withdrawn

    uint256 public depositLockSeconds; // global lock duration applied to each deposit entry

    // --- Borrower loan state ---
    struct Loan {
        uint256 principal;
        uint256 outstanding;
        uint256 startTime;
        uint256 dueTime;
        bool active;
    }
    mapping(address => Loan) public loans;

    // --- Ban list ---
    mapping(address => bool) public banned;

    // --- Events ---
    event Deposited(address indexed lender, uint256 amount, uint256 timestamp);
    event Withdrawn(address indexed lender, uint256 amount);
    event LoanOpened(address indexed borrower, uint256 principal, uint256 startTime, uint256 dueTime);
    event LoanRepaid(address indexed borrower, uint256 amount, uint256 remaining);
    event BorrowerBanned(address indexed borrower);
    event BorrowerUnbanned(address indexed borrower);
    event TrustMintSbtUpdated(address indexed sbt);
    event MinScoreUpdated(uint256 minScore);
    event DepositLockUpdated(uint256 lockSeconds);

    constructor(address initialOwner) Ownable(initialOwner) {}

    // --- Configuration ---
    function setTrustMintSbt(address sbt) external onlyOwner {
        trustMintSbt = sbt;
        emit TrustMintSbtUpdated(sbt);
    }

    function setMinScoreToBorrow(uint256 newMinScore) external onlyOwner {
        minScoreToBorrow = newMinScore;
        emit MinScoreUpdated(newMinScore);
    }

    function setDepositLockSeconds(uint256 seconds_) external onlyOwner {
        depositLockSeconds = seconds_;
        emit DepositLockUpdated(seconds_);
    }

    // --- Lender actions ---

    /**
     * @notice Deposit native token (USDC on Arc) into the pool.
     * @param amount Amount in wei; must equal msg.value.
     */
    function deposit(uint256 amount) external payable nonReentrant {
        require(amount > 0, "amount=0");
        require(msg.value == amount, "msg.value mismatch");
        require(amount <= type(uint128).max, "amount too large");

        uint128 amount128 = SafeCast.toUint128(amount);
        uint64 timestamp64 = SafeCast.toUint64(block.timestamp);

        _deposits[msg.sender].push(DepositEntry({amount: amount128, timestamp: timestamp64}));
        totalDeposited[msg.sender] += amount;
        totalDeposits += amount;

        emit Deposited(msg.sender, amount, block.timestamp);
    }

    /**
     * @notice Withdraw unlocked funds that have completed the lock period.
     * @param amount Amount to withdraw in wei.
     */
    function withdraw(uint256 amount) external nonReentrant {
        require(amount > 0, "amount=0");
        uint256 remaining = amount;
        uint256 idx = nextWithdrawalIndex[msg.sender];
        DepositEntry[] storage entries = _deposits[msg.sender];

        while (remaining > 0) {
            require(idx < entries.length, "insufficient balance");
            DepositEntry storage entry = entries[idx];
            require(entry.amount > 0, "entry empty");
            require(block.timestamp >= entry.timestamp + depositLockSeconds, "locked");

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

        require(address(this).balance >= amount, "pool liquidity low");
        (bool sent, ) = msg.sender.call{value: amount}("");
        require(sent, "transfer failed");

        emit Withdrawn(msg.sender, amount);
    }

    // --- Borrower actions ---

    /**
     * @notice Issue a loan to `borrower` and send principal immediately. Callable by owner/governance.
     * @param borrower Borrower wallet
     * @param principal Principal in wei
     * @param termSeconds Loan duration in seconds
     */
    function openLoan(address borrower, uint256 principal, uint256 termSeconds) external onlyOwner nonReentrant {
        require(!banned[borrower], "borrower banned");
        require(principal > 0, "principal=0");
        require(termSeconds > 0, "term=0");
        require(address(this).balance >= principal, "insufficient liquidity");

        if (trustMintSbt != address(0)) {
            ITrustMintSBT sbt = ITrustMintSBT(trustMintSbt);
            require(sbt.hasSbt(borrower), "borrower lacks TrustMint SBT");
            (uint256 score,, bool valid) = sbt.getScore(borrower);
            require(valid, "invalid score");
            require(score >= minScoreToBorrow, "score too low");
        }

        Loan storage loan = loans[borrower];
        require(!loan.active || loan.outstanding == 0, "borrower has unpaid loan");

        loan.principal = principal;
        loan.outstanding = principal;
        loan.startTime = block.timestamp;
        loan.dueTime = block.timestamp + termSeconds;
        loan.active = true;

        (bool sent, ) = payable(borrower).call{value: principal}("");
        require(sent, "loan transfer failed");

        emit LoanOpened(borrower, principal, loan.startTime, loan.dueTime);
    }

    /**
     * @notice Repay part or all of an outstanding loan by sending native token.
     * @param amount Amount in wei; must equal msg.value.
     */
    function repay(uint256 amount) external payable nonReentrant {
        Loan storage loan = loans[msg.sender];
        require(loan.active, "no active loan");
        require(amount > 0, "amount=0");
        require(msg.value == amount, "msg.value mismatch");
        require(amount <= loan.outstanding, "repay > outstanding");

        loan.outstanding -= amount;
        if (loan.outstanding == 0) {
            loan.active = false;
        }

        emit LoanRepaid(msg.sender, amount, loan.outstanding);
    }

    // --- Ban management ---
    function checkDefaultAndBan(address borrower) external {
        Loan storage loan = loans[borrower];
        if (loan.active && block.timestamp > loan.dueTime && loan.outstanding > 0 && !banned[borrower]) {
            banned[borrower] = true;
            emit BorrowerBanned(borrower);
        }
    }

    function unban(address borrower) external onlyOwner {
        require(banned[borrower], "not banned");
        banned[borrower] = false;
        emit BorrowerUnbanned(borrower);
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

    function previewWithdraw(address lender) external view returns (uint256 unlockable) {
        DepositEntry[] storage entries = _deposits[lender];
        uint256 idx = nextWithdrawalIndex[lender];
        uint256 len = entries.length;
        uint256 current = block.timestamp;
        uint256 lockPeriod = depositLockSeconds;

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

    function availableLiquidity() public view returns (uint256) {
        return address(this).balance;
    }
}
