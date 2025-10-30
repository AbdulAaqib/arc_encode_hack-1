// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import { IERC20 } from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";


/**
 * @title CreditLineManager
 * @notice Manages issuance and repayment of credit lines denominated in a STABLECOIN.
 */
contract CreditLineManager is Ownable {
    IERC20 public immutable STABLECOIN;  // USDC or other STABLECOIN

    struct CreditLine {
        uint256 limit;         // Maximum amount the borrower can draw
        uint256 drawn;         // Amount already drawn
        uint256 interestRate;  // Annual interest rate in basis points (e.g., 500 = 5.00%)
        uint256 lastDrawnTime; // Timestamp of last draw/interest calc
        bool active;
    }

    mapping(address => CreditLine) public creditLines;

    event CreditLineCreated(address indexed borrower, uint256 limit, uint256 interestRate);
    event CreditDrawn(address indexed borrower, uint256 amount);
    event CreditRepaid(address indexed borrower, uint256 amount);
    event CreditLineClosed(address indexed borrower);

    constructor(IERC20 _stablecoin, address initialOwner) Ownable(initialOwner) {
        STABLECOIN = _stablecoin;
    }


    /**
     * @notice Create a credit line for a borrower.
     * @dev Only owner (or governance) can call.
     */
    function createCreditLine(address borrower, uint256 limit, uint256 interestRate) external onlyOwner {
        require(!creditLines[borrower].active, "Credit line exists");
        creditLines[borrower] = CreditLine({
            limit: limit,
            drawn: 0,
            interestRate: interestRate,
            lastDrawnTime: block.timestamp,
            active: true
        });
        emit CreditLineCreated(borrower, limit, interestRate);
    }

    /**
     * @notice Draw funds from your credit line.
     */
    function draw(address borrower, uint256 amount) external {
        require(creditLines[borrower].active, "No active credit line");
        CreditLine storage line = creditLines[borrower];
        require(amount + line.drawn <= line.limit, "Exceeds limit");

        // Calculate and collect interest if needed (simplified)
        // For demo: interest accrues on drawn amount since lastDrawnTime.

        line.drawn += amount;
        line.lastDrawnTime = block.timestamp;
        require(STABLECOIN.transfer(borrower, amount), "Transfer failed");

        emit CreditDrawn(borrower, amount);
    }

    /**
     * @notice Repay funds (principal + interest).
     */
    function repay(address borrower, uint256 amount) external {
        require(creditLines[borrower].active, "No active credit line");

        CreditLine storage line = creditLines[borrower];
        require(amount <= line.drawn, "Repaying more than drawn");

        // Simplified: no separate interest tracking in this demo
        line.drawn -= amount;
        // Update last time to current; for real implementation you'd track more
        line.lastDrawnTime = block.timestamp;
        require(STABLECOIN.transferFrom(msg.sender, address(this), amount), "TransferFrom failed");

        emit CreditRepaid(borrower, amount);
    }

    /**
     * @notice Close credit line once fully repaid.
     */
    function closeCreditLine(address borrower) external onlyOwner {
        CreditLine storage line = creditLines[borrower];
        require(line.active, "No active line");
        require(line.drawn == 0, "Outstanding debt exists");
        line.active = false;

        emit CreditLineClosed(borrower);
    }

    /**
     * @notice View available credit for a borrower.
     */
    function availableCredit(address borrower) external view returns (uint256) {
        CreditLine storage line = creditLines[borrower];
        if (!line.active) return 0;
        return line.limit - line.drawn;
    }
}
