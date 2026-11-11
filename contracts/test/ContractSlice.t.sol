// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ScoreRegistry} from "../src/ScoreRegistry.sol";
import {TestVault} from "../src/TestVault.sol";
import {
    ReactiveRiskGuard,
    IReactiveSystem,
    LogRecord,
    CallbackConfiguration_V_1_0
} from "../src/ReactiveRiskGuard.sol";

interface Vm {
    function prank(address sender) external;
    function deal(address account, uint256 balance) external;
}

/// @dev Test-only double shaped to the Reactive interfaces; not a live Reactive integration.
contract MockReactiveSystem is IReactiveSystem {
    uint256 public subscribedChain;
    address public subscribedContract;
    uint256[4] public subscribedTopics;
    uint256 public callbackCount;
    CallbackConfiguration_V_1_0 private lastCallback;
    bool public rejectPayments;

    receive() external payable {
        require(!rejectPayments, "provider rejects payment");
    }

    function setRejectPayments(bool reject) external {
        rejectPayments = reject;
    }

    function subscribe(uint256 chain, address source, uint256 topic0, uint256 topic1, uint256 topic2, uint256 topic3)
        external
    {
        subscribedChain = chain;
        subscribedContract = source;
        subscribedTopics = [topic0, topic1, topic2, topic3];
    }

    function requestCallbackV_1_0(CallbackConfiguration_V_1_0 calldata configuration) external {
        ++callbackCount;
        lastCallback = configuration;
    }

    function callbackConfiguration() external view returns (uint256, address, uint64, bytes memory) {
        return (lastCallback.chainId, lastCallback.recipient, lastCallback.gasLimit, lastCallback.payload);
    }

    function deliver(ReactiveRiskGuard guard, LogRecord calldata log) external {
        guard.react(log);
    }

    function executeCallback(ReactiveRiskGuard guard, TestVault vault) external {
        (, address recipient,, bytes memory payload) = this.callbackConfiguration();
        require(recipient == address(vault), "wrong callback recipient");
        (address injected, bytes32 windowId, bytes32 publicationTxHash, uint256 logIndex) =
            abi.decode(_stripSelector(payload), (address, bytes32, bytes32, uint256));
        require(injected == address(0), "missing sender placeholder");
        vault.pauseDeposits(address(guard), windowId, publicationTxHash, logIndex);
    }

    function collectReactive(ReactiveRiskGuard guard, uint256 amount) external {
        guard.pay(amount);
    }

    function collectVault(TestVault vault, uint256 amount) external {
        vault.pay(amount);
    }

    function tryCollectReactive(ReactiveRiskGuard guard, uint256 amount) external returns (bool success) {
        (success,) = address(guard).call(abi.encodeCall(guard.pay, (amount)));
    }

    function tryCollectVault(TestVault vault, uint256 amount) external returns (bool success) {
        (success,) = address(vault).call(abi.encodeCall(vault.pay, (amount)));
    }

    function _stripSelector(bytes memory callData) private pure returns (bytes memory args) {
        require(callData.length >= 4, "short callback payload");
        args = new bytes(callData.length - 4);
        for (uint256 i; i < args.length; ++i) args[i] = callData[i + 4];
    }
}

contract ContractSliceTest {
    Vm private constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));
    address private constant ALICE = address(0xA11CE);
    address private constant OWNER = address(0xB0B);
    uint256 private constant IGNORE = 0xa65f96fc951c35ead38878e0f0b7a3c744a6f5ccc1476b313353ce31712313ad;

    function testRegistryReporterBoundsUniquenessAndAuditableAuthorization() external {
        ScoreRegistry registry = new ScoreRegistry(OWNER, ALICE);
        require(registry.authorizedReporter(ALICE), "initial reporter");

        (bool unauthorized,) = address(registry).call(
            abi.encodeCall(registry.publishScore, (bytes32(uint256(1)), uint16(10), bytes32(0), bytes32(0)))
        );
        require(!unauthorized, "unauthorized publish accepted");

        vm.prank(ALICE);
        registry.publishScore(bytes32(uint256(1)), 10_000, bytes32(uint256(2)), bytes32(uint256(3)));
        require(registry.publishedWindow(bytes32(uint256(1))), "window not recorded");

        vm.prank(ALICE);
        (bool duplicate,) = address(registry).call(
            abi.encodeCall(registry.publishScore, (bytes32(uint256(1)), uint16(10), bytes32(0), bytes32(0)))
        );
        require(!duplicate, "duplicate accepted");

        vm.prank(OWNER);
        registry.setReporter(ALICE, false);
        require(!registry.authorizedReporter(ALICE), "reporter not revoked");

        vm.prank(ALICE);
        (bool revoked,) = address(registry).call(
            abi.encodeCall(registry.publishScore, (bytes32(uint256(4)), uint16(10), bytes32(0), bytes32(0)))
        );
        require(!revoked, "revoked reporter accepted");
    }

    function testRegistryRejectsScoreAboveMaximum() external {
        ScoreRegistry registry = new ScoreRegistry(OWNER, address(this));
        (bool ok,) = address(registry).call(
            abi.encodeCall(registry.publishScore, (bytes32(uint256(1)), uint16(10_001), bytes32(0), bytes32(0)))
        );
        require(!ok, "out of bounds score accepted");
    }

    function testRegistryRejectsMissingModelAndSourceMetadata() external {
        ScoreRegistry registry = new ScoreRegistry(address(this), address(this));
        (bool missingModel,) = address(registry).call(
            abi.encodeCall(registry.publishScore, (bytes32(uint256(1)), uint16(1), bytes32(0), bytes32(uint256(2))))
        );
        require(!missingModel, "zero model version accepted");
        (bool missingSource,) = address(registry).call(
            abi.encodeCall(registry.publishScore, (bytes32(uint256(2)), uint16(1), bytes32(uint256(1)), bytes32(0)))
        );
        require(!missingSource, "zero source reference accepted");
    }

    function testReactiveAbiWildcardThresholdReplayCallbackAndVaultPause() external {
        MockReactiveSystem system = new MockReactiveSystem();
        address registry = address(0x1234);
        ReactiveRiskGuard guard = new ReactiveRiskGuard(address(this), address(system), 84532, registry, 84532, 7000, 200_000);
        TestVault vault = new TestVault(OWNER, address(system), address(guard));
        guard.configureDestinationVault(address(vault));
        (bool redirected,) = address(guard).call(
            abi.encodeCall(guard.configureDestinationVault, (address(0x9876)))
        );
        require(!redirected, "destination changed after configuration");
        vm.prank(OWNER);
        (bool unauthorizedConfig,) = address(guard).call(
            abi.encodeCall(guard.configureDestinationVault, (address(0x9876)))
        );
        require(!unauthorizedConfig, "non-owner configured destination");

        require(system.subscribedChain() == 84532, "wrong source chain");
        require(system.subscribedContract() == registry, "wrong source contract");
        require(system.subscribedTopics(0) == guard.SCORE_PUBLISHED_TOPIC(), "wrong topic0");
        require(system.subscribedTopics(1) == IGNORE && system.subscribedTopics(2) == IGNORE, "wrong wildcard");
        require(system.subscribedTopics(3) == IGNORE, "wrong wildcard");

        LogRecord memory log = _scoreLog(84532, registry, 6999, bytes32(uint256(1)), 4);
        system.deliver(guard, log);
        require(system.callbackCount() == 0, "below threshold callback");

        log.data = abi.encode(uint16(7000), bytes32(uint256(9)));
        system.deliver(guard, log);
        require(system.callbackCount() == 1, "threshold callback missing");
        (uint256 callbackChain, address recipient, uint64 gasLimit, bytes memory payload) = system.callbackConfiguration();
        require(callbackChain == 84532 && recipient == address(vault) && gasLimit == 200_000, "callback config mismatch");
        require(_selector(payload) == TestVault.pauseDeposits.selector, "callback selector mismatch");
        (address senderPlaceholder, bytes32 windowId, bytes32 publicationTxHash, uint256 publicationLogIndex) =
            _decodePayload(payload);
        require(senderPlaceholder == address(0), "missing injection placeholder");
        require(windowId == bytes32(uint256(7)), "window provenance mismatch");
        require(publicationTxHash == bytes32(uint256(1)), "tx provenance mismatch");
        require(publicationLogIndex == 4, "log index mismatch");

        system.executeCallback(guard, vault);
        require(vault.depositsPaused(), "vault was not paused");
        system.deliver(guard, log);
        require(system.callbackCount() == 1, "replay triggered callback");
        log.chainId = 1;
        system.deliver(guard, log);
        require(system.callbackCount() == 1, "wrong chain accepted");
        log.chainId = 84532;
        log.contractAddress = address(0x9999);
        system.deliver(guard, log);
        require(system.callbackCount() == 1, "wrong registry accepted");

        (bool spoofed,) = address(guard).call(abi.encodeCall(guard.react, (log)));
        require(!spoofed, "non-system caller accepted");
    }

    function testReactiveRejectsMalformedMetadataAndInvalidEventShape() external {
        MockReactiveSystem system = new MockReactiveSystem();
        ReactiveRiskGuard guard = new ReactiveRiskGuard(address(this), address(system), 84532, address(0x1234), 84532, 1, 1);
        guard.configureDestinationVault(address(0x5678));
        LogRecord memory log = _scoreLog(84532, address(0x1234), 9000, bytes32(uint256(1)), 0);

        log.opCode = 3;
        system.deliver(guard, log);
        log.opCode = 4;
        log.topic2 = 0;
        system.deliver(guard, log);
        log.topic2 = uint256(2);
        log.data = abi.encode(uint16(9000));
        system.deliver(guard, log);
        log.data = abi.encode(uint16(9000), bytes32(0));
        system.deliver(guard, log);
        require(system.callbackCount() == 0, "invalid metadata accepted");
    }

    function testVaultCallbackAuthStickyPauseAndOwnerRecovery() external {
        address proxy = address(0xCA11);
        address reactive = address(0xBEEF);
        TestVault vault = new TestVault(address(this), proxy, reactive);
        vault.deposit();

        (bool directCall,) = address(vault).call(
            abi.encodeCall(vault.pauseDeposits, (reactive, bytes32(uint256(1)), bytes32(uint256(2)), uint256(3)))
        );
        require(!directCall, "direct callback accepted");

        vm.prank(proxy);
        (bool wrongSender,) = address(vault).call(
            abi.encodeCall(vault.pauseDeposits, (address(1), bytes32(uint256(1)), bytes32(uint256(2)), uint256(3)))
        );
        require(!wrongSender, "wrong reactive sender accepted");

        vm.prank(proxy);
        vault.pauseDeposits(reactive, bytes32(uint256(1)), bytes32(uint256(2)), 3);
        require(vault.depositsPaused(), "not paused");
        (bool depositOk,) = address(vault).call(abi.encodeCall(vault.deposit, ()));
        require(!depositOk, "deposit accepted while paused");

        vm.prank(proxy);
        vault.pauseDeposits(reactive, bytes32(uint256(9)), bytes32(uint256(8)), 7);
        require(vault.depositsPaused(), "pause not sticky");

        vm.prank(address(0xBAD));
        (bool unauthorizedUnpause,) = address(vault).call(abi.encodeCall(vault.unpauseDeposits, ()));
        require(!unauthorizedUnpause, "unauthorized unpause accepted");
        vault.unpauseDeposits();
        require(!vault.depositsPaused(), "owner could not unpause");
        vault.deposit();
    }

    function testReactiveAndVaultProviderOnlyPayAndFunding() external {
        MockReactiveSystem system = new MockReactiveSystem();
        ReactiveRiskGuard guard = new ReactiveRiskGuard(OWNER, address(system), 84532, address(0x1234), 84532, 1, 1);
        TestVault vault = new TestVault(OWNER, address(system), address(guard));

        (bool unauthorizedReactivePay,) = address(guard).call(abi.encodeCall(guard.pay, (uint256(1 ether))));
        require(!unauthorizedReactivePay, "unauthorized reactive pay accepted");
        (bool unauthorizedVaultPay,) = address(vault).call(abi.encodeCall(vault.pay, (uint256(1 ether))));
        require(!unauthorizedVaultPay, "unauthorized vault pay accepted");

        require(!system.tryCollectReactive(guard, 0.5 ether), "reactive payment succeeded without funds");
        require(!system.tryCollectVault(vault, 0.5 ether), "vault payment succeeded without funds");

        vm.deal(address(guard), 1 ether);
        vm.deal(address(vault), 1 ether);
        system.collectReactive(guard, 0.4 ether);
        system.collectVault(vault, 0.3 ether);
        require(address(system).balance == 0.7 ether, "provider balance not increased");
        require(address(guard).balance == 0.6 ether, "reactive balance not decreased");
        require(address(vault).balance == 0.7 ether, "vault balance not decreased");
        system.setRejectPayments(true);
        require(!system.tryCollectReactive(guard, 0.1 ether), "reactive ignored provider transfer failure");
        require(!system.tryCollectVault(vault, 0.1 ether), "vault ignored provider transfer failure");
        require(address(guard).balance == 0.6 ether, "failed reactive payment changed balance");
        require(address(vault).balance == 0.7 ether, "failed vault payment changed balance");
        (bool deposited,) = address(vault).call{value: 0}(abi.encodeCall(vault.deposit, ()));
        require(deposited, "no-value deposit failed");
    }

    function _scoreLog(uint256 chain, address source, uint16 score, bytes32 txHash, uint256 logIndex)
        private
        pure
        returns (LogRecord memory log)
    {
        log.chainId = chain;
        log.contractAddress = source;
        log.topic0 = uint256(keccak256("ScorePublished(bytes32,uint16,bytes32,bytes32,address)"));
        log.topic1 = 7;
        log.topic2 = 2;
        log.topic3 = uint256(uint160(ALICE));
        log.opCode = 4;
        log.blockHash = 11;
        log.txHash = uint256(txHash);
        log.logIndex = logIndex;
        log.data = abi.encode(score, bytes32(uint256(8)));
    }

    function _decodePayload(bytes memory payload)
        private
        pure
        returns (address, bytes32, bytes32, uint256)
    {
        bytes memory args = new bytes(payload.length - 4);
        for (uint256 i; i < args.length; ++i) args[i] = payload[i + 4];
        return abi.decode(args, (address, bytes32, bytes32, uint256));
    }

    function _selector(bytes memory payload) private pure returns (bytes4 selector) {
        require(payload.length >= 4, "short payload");
        assembly { selector := mload(add(payload, 32)) }
    }
}
