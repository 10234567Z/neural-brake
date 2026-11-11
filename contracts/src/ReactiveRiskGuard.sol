// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @dev Local declarations matching the Reactive ABI used by this slice. Kept dependency-free;
/// verify these against the pinned Reactive library before deployment.
struct LogRecord {
    uint256 chainId;
    address contractAddress;
    uint256 topic0;
    uint256 topic1;
    uint256 topic2;
    uint256 topic3;
    bytes data;
    uint256 blockNumber;
    uint256 opCode;
    uint256 blockHash;
    uint256 txHash;
    uint256 logIndex;
}

struct CallbackConfiguration_V_1_0 {
    uint256 chainId;
    address recipient;
    uint64 gasLimit;
    bytes payload;
}

interface IReactiveSystem {
    function subscribe(
        uint256 originChainId,
        address originContract,
        uint256 topic0,
        uint256 topic1,
        uint256 topic2,
        uint256 topic3
    ) external;

    function requestCallbackV_1_0(CallbackConfiguration_V_1_0 calldata configuration) external;
}

/// @notice Filters ScoreRegistry logs on Reactive and requests vault callbacks for threshold crossings.
contract ReactiveRiskGuard {
    uint256 public constant SCORE_PUBLISHED_TOPIC =
        uint256(keccak256("ScorePublished(bytes32,uint16,bytes32,bytes32,address)"));
    uint256 public constant REACTIVE_IGNORE =
        0xa65f96fc951c35ead38878e0f0b7a3c744a6f5ccc1476b313353ce31712313ad;
    uint256 public constant MAX_SCORE = 10_000;

    address public immutable owner;
    address public immutable system;
    uint256 public immutable originChainId;
    address public immutable originRegistry;
    uint256 public immutable destinationChainId;
    uint16 public immutable threshold;
    uint64 public immutable callbackGasLimit;
    address public destinationVault;
    bool public destinationConfigured;

    mapping(bytes32 => bool) public processedLog;

    error Unauthorized();
    error InvalidConfiguration();
    error DestinationAlreadyConfigured();
    error InsufficientBalance(uint256 available, uint256 requested);
    error PaymentTransferFailed();

    event DestinationConfigured(uint256 indexed chainId, address indexed vault);
    event Paid(address indexed serviceProvider, uint256 amount);

    constructor(
        address initialOwner,
        address systemAddress,
        uint256 sourceChain,
        address scoreRegistry,
        uint256 destinationChain,
        uint16 scoreThreshold,
        uint64 gasLimit
    ) payable {
        if (
            initialOwner == address(0) || systemAddress == address(0) || scoreRegistry == address(0)
                || scoreThreshold > MAX_SCORE || gasLimit == 0
        ) revert InvalidConfiguration();
        owner = initialOwner;
        system = systemAddress;
        originChainId = sourceChain;
        originRegistry = scoreRegistry;
        destinationChainId = destinationChain;
        threshold = scoreThreshold;
        callbackGasLimit = gasLimit;

        // The score event has three indexed fields; REACTIVE_IGNORE is the documented wildcard.
        IReactiveSystem(systemAddress).subscribe(
            sourceChain,
            scoreRegistry,
            SCORE_PUBLISHED_TOPIC,
            REACTIVE_IGNORE,
            REACTIVE_IGNORE,
            REACTIVE_IGNORE
        );
    }

    receive() external payable {}

    /// @notice Configure destination after deploying the Reactive contract and then its vault.
    function configureDestinationVault(address vault) external {
        if (msg.sender != owner) revert Unauthorized();
        if (vault == address(0)) revert InvalidConfiguration();
        if (destinationConfigured) revert DestinationAlreadyConfigured();
        destinationVault = vault;
        destinationConfigured = true;
        emit DestinationConfigured(destinationChainId, vault);
    }

    /// @notice Pay the configured Reactive service provider from this contract's native balance.
    function pay(uint256 amount) external {
        if (msg.sender != system) revert Unauthorized();
        uint256 available = address(this).balance;
        if (available < amount) revert InsufficientBalance(available, amount);
        (bool success,) = payable(msg.sender).call{value: amount}("");
        if (!success) revert PaymentTransferFailed();
        emit Paid(msg.sender, amount);
    }

    /// @notice Reactive System entry point; its tuple types must match the Reactive ABI exactly.
    function react(LogRecord calldata log) external {
        if (msg.sender != system) revert Unauthorized();
        if (
            log.chainId != originChainId || log.contractAddress != originRegistry
                || log.topic0 != SCORE_PUBLISHED_TOPIC
        ) return;
        // ScorePublished has topic0 plus 3 indexed values and data = abi.encode(uint16,bytes32).
        if (log.opCode != 4 || log.data.length != 64 || log.txHash == 0 || log.blockHash == 0) return;
        if (log.topic1 == 0 || log.topic2 == 0 || log.topic3 == 0) return;

        uint16 score;
        bytes32 sourceRef;
        (score, sourceRef) = abi.decode(log.data, (uint16, bytes32));
        if (uint256(score) > MAX_SCORE || sourceRef == bytes32(0) || score < threshold) return;
        if (!destinationConfigured) return;

        bytes32 windowId = bytes32(log.topic1);
        bytes32 publicationTxHash = bytes32(log.txHash);
        // topic3 is the indexed reporter address padded to 32 bytes; reject non-canonical values.
        if (log.topic3 >> 160 != 0 || address(uint160(log.topic3)) == address(0)) return;

        bytes32 replayKey = keccak256(abi.encode(log.chainId, log.txHash, log.logIndex));
        if (processedLog[replayKey]) return;
        processedLog[replayKey] = true;

        // Callback proxy injects the Reactive sender in the first argument, replacing address(0).
        bytes memory payload = abi.encodeWithSelector(
            bytes4(keccak256("pauseDeposits(address,bytes32,bytes32,uint256)")),
            address(0),
            windowId,
            publicationTxHash,
            log.logIndex
        );
        IReactiveSystem(system).requestCallbackV_1_0(
            CallbackConfiguration_V_1_0(destinationChainId, destinationVault, callbackGasLimit, payload)
        );
    }
}
