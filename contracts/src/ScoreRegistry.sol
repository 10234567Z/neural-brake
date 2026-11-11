// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Stores reporter-published risk scores. This contract does not attest to inference.
contract ScoreRegistry {
    uint16 public constant MAX_SCORE = 10_000;

    address public owner;
    mapping(address => bool) public authorizedReporter;
    mapping(bytes32 => bool) public publishedWindow;

    event ReporterAuthorizationChanged(address indexed reporter, bool authorized, address indexed changedBy);
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event ScorePublished(
        bytes32 indexed windowId,
        uint16 score,
        bytes32 indexed modelVersion,
        bytes32 sourceRef,
        address indexed reporter
    );

    error Unauthorized();
    error ZeroAddress();
    error InvalidScore(uint256 score);
    error EmptyWindowId();
    error DuplicateWindow(bytes32 windowId);
    error InvalidMetadata();

    constructor(address initialOwner, address initialReporter) {
        if (initialOwner == address(0) || initialReporter == address(0)) revert ZeroAddress();
        owner = initialOwner;
        emit OwnershipTransferred(address(0), initialOwner);
        authorizedReporter[initialReporter] = true;
        emit ReporterAuthorizationChanged(initialReporter, true, initialOwner);
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert Unauthorized();
        _;
    }

    function setReporter(address reporter, bool authorized) external onlyOwner {
        if (reporter == address(0)) revert ZeroAddress();
        authorizedReporter[reporter] = authorized;
        emit ReporterAuthorizationChanged(reporter, authorized, msg.sender);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    function publishScore(bytes32 windowId, uint16 score, bytes32 modelVersion, bytes32 sourceRef) external {
        if (!authorizedReporter[msg.sender]) revert Unauthorized();
        if (score > MAX_SCORE) revert InvalidScore(score);
        if (windowId == bytes32(0)) revert EmptyWindowId();
        if (modelVersion == bytes32(0) || sourceRef == bytes32(0)) revert InvalidMetadata();
        if (publishedWindow[windowId]) revert DuplicateWindow(windowId);

        publishedWindow[windowId] = true;
        emit ScorePublished(windowId, score, modelVersion, sourceRef, msg.sender);
    }
}
