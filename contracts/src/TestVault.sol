// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice No-value deposit gate for local/testnet circuit-breaker demonstrations.
contract TestVault {
    address public immutable owner;
    address public immutable callbackProxy;
    address public immutable reactiveSender;
    bool public depositsPaused;

    error Unauthorized();
    error DepositsArePaused();
    error ZeroAddress();
    error InsufficientBalance(uint256 available, uint256 requested);
    error PaymentTransferFailed();

    event Deposited(address indexed account);
    event DepositsPaused(
        address indexed reactiveSender,
        bytes32 indexed windowId,
        bytes32 indexed publicationTxHash,
        uint256 publicationLogIndex
    );
    event DepositsUnpaused(address indexed owner);
    event Paid(address indexed serviceProvider, uint256 amount);

    constructor(address initialOwner, address proxy, address expectedReactiveSender) payable {
        if (initialOwner == address(0) || proxy == address(0) || expectedReactiveSender == address(0)) {
            revert ZeroAddress();
        }
        owner = initialOwner;
        callbackProxy = proxy;
        reactiveSender = expectedReactiveSender;
    }

    receive() external payable {}

    /// @dev Intentionally nonpayable and accepts no amount or asset; this vault holds no value.
    function deposit() external {
        if (depositsPaused) revert DepositsArePaused();
        emit Deposited(msg.sender);
    }

    /// @notice Reactive callback entry point. First argument is injected by the callback proxy.
    function pauseDeposits(
        address injectedReactiveSender,
        bytes32 windowId,
        bytes32 publicationTxHash,
        uint256 publicationLogIndex
    ) external {
        if (msg.sender != callbackProxy || injectedReactiveSender != reactiveSender) revert Unauthorized();
        if (!depositsPaused) {
            depositsPaused = true;
            emit DepositsPaused(injectedReactiveSender, windowId, publicationTxHash, publicationLogIndex);
        }
    }

    /// @notice Pay the callback service provider from this contract's native balance.
    function pay(uint256 amount) external {
        if (msg.sender != callbackProxy) revert Unauthorized();
        uint256 available = address(this).balance;
        if (available < amount) revert InsufficientBalance(available, amount);
        (bool success,) = payable(msg.sender).call{value: amount}("");
        if (!success) revert PaymentTransferFailed();
        emit Paid(msg.sender, amount);
    }

    /// @notice Recovery is deliberately explicit and owner-controlled; never automatic.
    function unpauseDeposits() external {
        if (msg.sender != owner) revert Unauthorized();
        if (depositsPaused) {
            depositsPaused = false;
            emit DepositsUnpaused(msg.sender);
        }
    }
}
