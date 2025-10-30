// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {Strings} from "@openzeppelin/contracts/utils/Strings.sol";

/// @title ERC-5192 Minimal Soulbound NFT interface
/// @dev https://eips.ethereum.org/EIPS/eip-5192
interface IERC5192 {
    /// @notice Emitted when a token is locked.
    event Locked(uint256 tokenId);
    /// @notice Emitted when a token is unlocked.
    event Unlocked(uint256 tokenId);
    /// @notice Returns true if a token is locked (non-transferable).
    function locked(uint256 tokenId) external view returns (bool);
}

/// @title TrustMintSBT
/// @notice Non-transferable ERC-721 that binds a dynamic credit score to a wallet.
/// @dev One token per wallet. Transfer and approvals are disabled. Owner acts as issuer.
contract TrustMintSBT is ERC721, Ownable, IERC5192 {
    struct Score {
        uint256 value;
        uint256 timestamp;
        bool valid;
    }

    // Public mapping mirrors CreditScoreRegistry for compatibility with existing tooling
    mapping(address => Score) public scores;

    // --- Metadata base (hardcoded IPFS) ---
    // Replace "ipfs://YOUR_CID/" with your actual IPFS CID folder containing <tokenId>.json files
    string private constant _BASE_IPFS = "ipfs://bafkreieueoc427a45lgw5xtogabku24s7ca5vxysk4swwe6bf5w7mrjpnm/";

    // --- Events ---
    event ScoreIssued(address indexed borrower, uint256 value, uint256 timestamp);
    event ScoreRevoked(address indexed borrower);

    constructor(string memory name_, string memory symbol_, address initialOwner)
        ERC721(name_, symbol_)
        Ownable(initialOwner)
    {}

    // --- Soulbound mechanics (ERC-5192) ---
    function locked(uint256 tokenId) external view override returns (bool) {
        // Token is always non-transferable once minted
        return _ownerOf(tokenId) != address(0);
    }

    function supportsInterface(bytes4 interfaceId)
        public
        view
        override(ERC721)
        returns (bool)
    {
        return interfaceId == type(IERC5192).interfaceId || super.supportsInterface(interfaceId);
    }

    // --- SBT policy: block any transfer/burn via OZ v5 internal hook ---
    // Allow only mints (from == address(0)), disallow transfers and burns.
    function _update(address to, uint256 tokenId, address auth)
        internal
        override
        returns (address previousOwner)
    {
        address from = _ownerOf(tokenId);
        // Disallow transfers (existing token moving between two non-zero addresses)
        if (from != address(0) && to != address(0)) {
            revert("SBT: non-transferable");
        }
        // Disallow burns (to == address(0))
        if (to == address(0)) {
            revert("SBT: non-burnable");
        }
        previousOwner = super._update(to, tokenId, auth);
    }

    // --- TokenId derivation: one token per wallet (deterministic) ---
    function tokenIdOf(address wallet) public pure returns (uint256) {
        return uint256(uint160(wallet));
    }

    function hasSbt(address wallet) public view returns (bool) {
        return _ownerOf(tokenIdOf(wallet)) != address(0);
    }

    // --- Metadata: hardcoded IPFS <base>/<tokenId>.json ---
    function tokenURI(uint256 tokenId) public view override returns (string memory) {
        require(_ownerOf(tokenId) != address(0), "SBT: not minted");
        return string(abi.encodePacked(_BASE_IPFS, Strings.toString(tokenId), ".json"));
    }

    // --- Issuer functions ---

    /// @notice Mint an SBT to `borrower` (if not exists) and set/update the score.
    function issueScore(address borrower, uint256 value) external onlyOwner {
        uint256 tid = tokenIdOf(borrower);
        if (_ownerOf(tid) == address(0)) {
            _safeMint(borrower, tid);
            emit Locked(tid);
        }
        scores[borrower] = Score({ value: value, timestamp: block.timestamp, valid: true });
        emit ScoreIssued(borrower, value, block.timestamp);
    }

    /// @notice Revoke the score for `borrower` (token remains soulbound but flagged invalid).
    function revokeScore(address borrower) external onlyOwner {
        require(_ownerOf(tokenIdOf(borrower)) != address(0), "SBT: not minted");
        scores[borrower].valid = false;
        emit ScoreRevoked(borrower);
        // Note: token remains locked and bound; not emitting Unlocked per ERC-5192 since it remains non-transferable.
    }

    /// @notice Read the score tuple mirroring the registry view for compatibility.
    function getScore(address borrower)
        external
        view
        returns (uint256 value, uint256 timestamp, bool valid)
    {
        Score memory s = scores[borrower];
        return (s.value, s.timestamp, s.valid);
    }
}
