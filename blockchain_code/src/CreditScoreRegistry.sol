// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import { Ownable } from "@openzeppelin/contracts/access/Ownable.sol";

contract CreditScoreRegistry is Ownable {
    struct Score {
        uint256 value;
        uint256 timestamp;
        bool valid;
    }

    mapping(address => Score) public scores;

    event ScoreIssued(address indexed borrower, uint256 value, uint256 timestamp);
    event ScoreRevoked(address indexed borrower);

    constructor(address initialOwner) Ownable(initialOwner) {}

    function issueScore(address borrower, uint256 value) external onlyOwner {
        scores[borrower] = Score({ value: value, timestamp: block.timestamp, valid: true });
        emit ScoreIssued(borrower, value, block.timestamp);
    }

    function revokeScore(address borrower) external onlyOwner {
        scores[borrower].valid = false;
        emit ScoreRevoked(borrower);
    }

    function getScore(address borrower)
        external
        view
        returns (uint256 value, uint256 timestamp, bool valid)
    {
        Score memory s = scores[borrower];
        return (s.value, s.timestamp, s.valid);
    }
}
