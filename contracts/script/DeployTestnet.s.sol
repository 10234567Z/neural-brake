// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ScoreRegistry} from "../src/ScoreRegistry.sol";
import {ReactiveRiskGuard} from "../src/ReactiveRiskGuard.sol";
import {TestVault} from "../src/TestVault.sol";

interface VmScript {
    function envAddress(string calldata name) external returns (address);
    function envUint(string calldata name) external returns (uint256);
    function startBroadcast() external;
    function stopBroadcast() external;
}

/// @notice Manual testnet deployment steps. No step runs unless an operator invokes it;
/// `forge script` is simulation-only unless `--broadcast` is explicitly supplied.
contract DeployTestnet {
    VmScript private constant VM = VmScript(address(uint160(uint256(keccak256("hevm cheat code")))));

    uint256 private constant BASE_SEPOLIA = 84532;
    uint256 private constant REACTIVE_LASNA = 5318007;
    address private constant REACTIVE_SYSTEM = 0x8888888888888888888888888888888888888888;
    address private constant BASE_CALLBACK_PROXY = 0x476F44b11F5C942c58f4c8E3d0300E5710743585;
    uint256 private constant MAX_SCORE = 10_000;
    uint256 private constant MIN_CALLBACK_GAS = 100_000;
    uint256 private constant MAX_CALLBACK_GAS = 900_000;

    error WrongChain(uint256 expected, uint256 actual);
    error InvalidDemoConfiguration();

    event ScoreRegistryDeployed(address indexed registry, address indexed owner, address indexed reporter);
    event ReactiveGuardDeployed(address indexed guard, address indexed owner, address indexed registry);
    event TestVaultDeployed(address indexed vault, address indexed owner, address indexed reactiveGuard);
    event DestinationConfigured(address indexed guard, address indexed vault);

    /// @notice Deploy on Base Sepolia before deploying the Reactive guard on Lasna.
    function deployScoreRegistry() external returns (address registryAddress) {
        _requireChain(BASE_SEPOLIA);
        address owner = _testnetOwner();
        address reporter = VM.envAddress("NB_TESTNET_REPORTER");
        if (reporter == address(0)) revert InvalidDemoConfiguration();

        VM.startBroadcast();
        ScoreRegistry registry = new ScoreRegistry(owner, reporter);
        VM.stopBroadcast();
        registryAddress = address(registry);
        emit ScoreRegistryDeployed(registryAddress, owner, reporter);
    }

    /// @notice Deploy on Reactive Lasna after ScoreRegistry exists on Base Sepolia.
    function deployReactiveGuard() external returns (address guardAddress) {
        _requireChain(REACTIVE_LASNA);
        address owner = _testnetOwner();
        address registry = VM.envAddress("NB_SCORE_REGISTRY_ADDRESS");
        uint256 threshold = VM.envUint("NB_REACTIVE_THRESHOLD");
        uint256 callbackGas = VM.envUint("NB_CALLBACK_GAS_LIMIT");
        if (
            owner == address(0) || registry == address(0) || threshold > MAX_SCORE
                || callbackGas < MIN_CALLBACK_GAS || callbackGas > MAX_CALLBACK_GAS
        ) revert InvalidDemoConfiguration();
        // forge-lint: disable-next-line(unsafe-typecast) -- threshold is bounded by MAX_SCORE above.
        uint16 checkedThreshold = uint16(threshold);
        // forge-lint: disable-next-line(unsafe-typecast) -- callbackGas is bounded by MAX_CALLBACK_GAS above.
        uint64 checkedCallbackGas = uint64(callbackGas);

        VM.startBroadcast();
        ReactiveRiskGuard guard = new ReactiveRiskGuard(
            owner, REACTIVE_SYSTEM, BASE_SEPOLIA, registry, BASE_SEPOLIA, checkedThreshold, checkedCallbackGas
        );
        VM.stopBroadcast();
        guardAddress = address(guard);
        emit ReactiveGuardDeployed(guardAddress, owner, registry);
    }

    /// @notice Deploy the explicitly no-value destination on Base Sepolia.
    function deployTestVault() external returns (address vaultAddress) {
        _requireChain(BASE_SEPOLIA);
        address owner = _testnetOwner();
        address guard = VM.envAddress("NB_REACTIVE_GUARD_ADDRESS");
        if (guard == address(0)) revert InvalidDemoConfiguration();

        VM.startBroadcast();
        TestVault vault = new TestVault(owner, BASE_CALLBACK_PROXY, guard);
        VM.stopBroadcast();
        vaultAddress = address(vault);
        emit TestVaultDeployed(vaultAddress, owner, guard);
    }

    /// @notice Complete the cycle-free destination configuration on Reactive Lasna.
    function configureDestination() external {
        _requireChain(REACTIVE_LASNA);
        _testnetOwner();
        address guardAddress = VM.envAddress("NB_REACTIVE_GUARD_ADDRESS");
        address vaultAddress = VM.envAddress("NB_TEST_VAULT_ADDRESS");
        if (guardAddress == address(0) || vaultAddress == address(0)) revert InvalidDemoConfiguration();

        VM.startBroadcast();
        ReactiveRiskGuard(payable(guardAddress)).configureDestinationVault(vaultAddress);
        VM.stopBroadcast();
        emit DestinationConfigured(guardAddress, vaultAddress);
    }

    function _requireChain(uint256 expected) private view {
        if (block.chainid != expected) revert WrongChain(expected, block.chainid);
    }

    function _testnetOwner() private returns (address owner) {
        owner = VM.envAddress("NB_TESTNET_OWNER");
        if (owner == address(0) || owner != tx.origin) revert InvalidDemoConfiguration();
    }
}
