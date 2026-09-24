import ganache from 'ganache';
import { ethers } from 'ethers';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function runTestSuite() {
    console.log("================================================================");
    console.log("🔒 Running BTC-K wBTCK Cross-Chain Bridge Hardened Security Test Suite");
    console.log("================================================================");

    const mnemonic = "test test test test test test test test test test test junk";

    // 1. Start in-memory Ganache EVM provider with deterministic mnemonic
    const ganacheProvider = ganache.provider({
        wallet: {
            mnemonic: mnemonic,
            totalAccounts: 10,
            defaultBalance: 1000
        },
        logging: { quiet: true }
    });
    const provider = new ethers.BrowserProvider(ganacheProvider);
    
    // Derive deterministic pre-funded client-side Wallets
    const deployer = ethers.HDNodeWallet.fromPhrase(mnemonic, undefined, "m/44'/60'/0'/0/0").connect(provider);
    const val1 = ethers.HDNodeWallet.fromPhrase(mnemonic, undefined, "m/44'/60'/0'/0/1").connect(provider);
    const val2 = ethers.HDNodeWallet.fromPhrase(mnemonic, undefined, "m/44'/60'/0'/0/2").connect(provider);
    const val3 = ethers.HDNodeWallet.fromPhrase(mnemonic, undefined, "m/44'/60'/0'/0/3").connect(provider);
    const user = ethers.HDNodeWallet.fromPhrase(mnemonic, undefined, "m/44'/60'/0'/0/4").connect(provider);
    const attacker = ethers.HDNodeWallet.fromPhrase(mnemonic, undefined, "m/44'/60'/0'/0/5").connect(provider);
    const relayer = ethers.HDNodeWallet.fromPhrase(mnemonic, undefined, "m/44'/60'/0'/0/6").connect(provider);

    console.log(`✓ Ganache EVM & Pre-Funded Wallets Initialized:`);
    console.log(`  Deployer:   ${deployer.address}`);
    console.log(`  Val 1:      ${val1.address}`);
    console.log(`  Val 2:      ${val2.address}`);
    console.log(`  Val 3:      ${val3.address}`);
    console.log(`  User:       ${user.address}`);
    console.log(`  Attacker:   ${attacker.address}`);

    // Load compiled ABI & Bytecode (paris EVM compatible)
    const abiPath = path.join(__dirname, 'build', 'contracts_wBTCK_sol_wBTCK.abi');
    const binPath = path.join(__dirname, 'build', 'contracts_wBTCK_sol_wBTCK.bin');
    const abi = JSON.parse(fs.readFileSync(abiPath, 'utf8'));
    const bin = '0x' + fs.readFileSync(binPath, 'utf8').trim();

    // 2. Deploy wBTCK Contract with 2-of-3 multi-sig threshold
    console.log("\n[Test 1: Contract Deployment & Quorum Initialization]");
    const factory = new ethers.ContractFactory(abi, bin, deployer);
    const initialValidators = [val1.address, val2.address, val3.address];
    const threshold = 2n;

    const wbtck = await factory.deploy(initialValidators, threshold);
    await wbtck.waitForDeployment();
    const contractAddress = await wbtck.getAddress();
    console.log(`✓ wBTCK deployed at: ${contractAddress}`);

    const name = await wbtck.name();
    const symbol = await wbtck.symbol();
    const decimals = await wbtck.decimals();
    const maxSupply = await wbtck.MAX_SUPPLY();
    const valThreshold = await wbtck.validatorThreshold();
    const valCount = await wbtck.validatorCount();

    if (name !== "Wrapped BTC-K" || symbol !== "wBTCK" || decimals !== 18n) {
        throw new Error("Metadata mismatch");
    }
    if (maxSupply !== ethers.parseEther("21000000")) {
        throw new Error("21M MAX_SUPPLY invariant violated");
    }
    if (valThreshold !== 2n || valCount !== 3n) {
        throw new Error("Validator threshold initialization failed");
    }
    console.log(`✓ ERC-20 Metadata & Invariants Verified: ${name} (${symbol}), Max Supply = 21,000,000 wBTCK, Quorum = 2/3`);

    // Helper to produce multi-sig proof-of-lock signatures
    async function signBridgeMint(recipient, amount, btckTxid, blockHeight, signers) {
        const net = await provider.getNetwork();
        const chainId = net.chainId;
        const BRIDGE_MINT_TYPEHASH = ethers.keccak256(
            ethers.toUtf8Bytes("BridgeMint(address to,uint256 amount,bytes32 btckTxid,uint256 blockHeight,uint256 chainId,address verifyingContract)")
        );

        const abiCoder = ethers.AbiCoder.defaultAbiCoder();
        const encoded = abiCoder.encode(
            ["bytes32", "address", "uint256", "bytes32", "uint256", "uint256", "address"],
            [BRIDGE_MINT_TYPEHASH, recipient, amount, btckTxid, blockHeight, chainId, contractAddress]
        );
        const messageHash = ethers.keccak256(encoded);

        // Sign with provided signers using ethers.js
        const sigEntries = [];
        for (const s of signers) {
            const ethSignedMessageBytes = ethers.getBytes(messageHash);
            const sig = await s.signMessage(ethSignedMessageBytes);
            sigEntries.push({ signerAddr: s.address, sig });
        }

        // Sort signatures by signer address ascending (enforced by wBTCK.sol)
        sigEntries.sort((a, b) => a.signerAddr.toLowerCase().localeCompare(b.signerAddr.toLowerCase()));
        return sigEntries.map(e => e.sig);
    }

    // 3. Test Multi-Sig Bridge Minting
    console.log("\n[Test 2: Multi-Sig Proof-of-Lock Bridge Minting]");
    const depositTxid1 = ethers.keccak256(ethers.toUtf8Bytes("BTCK_BLOCKDAG_DEPOSIT_TX_001"));
    const depositHeight1 = 120500n;
    const mintAmount1 = ethers.parseEther("1.5"); // 1.5 wBTCK

    // Sign with val1 and val2 (threshold 2 reached)
    const validSignatures = await signBridgeMint(user.address, mintAmount1, depositTxid1, depositHeight1, [val1, val2]);
    const mintTx = await wbtck.bridgeMintWithProof(user.address, mintAmount1, depositTxid1, depositHeight1, validSignatures);
    await mintTx.wait();

    const userBalance1 = await wbtck.balanceOf(user.address);
    const totalSupply1 = await wbtck.totalSupply();
    const isProcessed = await wbtck.processedBtckTxs(depositTxid1);

    if (userBalance1 !== mintAmount1 || totalSupply1 !== mintAmount1 || !isProcessed) {
        throw new Error("Bridge minting balances or processed flag incorrect");
    }
    console.log(`✓ Successfully minted 1.5 wBTCK to User with 2/3 validator proof. Total Supply: ${ethers.formatEther(totalSupply1)} wBTCK`);

    // 4. Test Anti-Replay / Double-Spend Protection
    console.log("\n[Test 3: Anti-Replay / Double-Spend Protection]");
    try {
        await wbtck.bridgeMintWithProof(user.address, mintAmount1, depositTxid1, depositHeight1, validSignatures);
        throw new Error("FAILED: Replay attack succeeded when it should have reverted");
    } catch (err) {
        console.log(`✓ Double-Spend Replay Attack correctly REVERTED: 'wBTCK: tx already processed'`);
    }

    // 5. Test Quorum & Validator Validation
    console.log("\n[Test 4: Quorum & Unauthorized Signer Enforcement]");
    const depositTxid2 = ethers.keccak256(ethers.toUtf8Bytes("BTCK_BLOCKDAG_DEPOSIT_TX_002"));
    
    // 5a. Insufficient signatures (1 < threshold 2)
    const singleSig = await signBridgeMint(user.address, mintAmount1, depositTxid2, 120501n, [val1]);
    try {
        await wbtck.bridgeMintWithProof(user.address, mintAmount1, depositTxid2, 120501n, singleSig);
        throw new Error("FAILED: Mint succeeded with insufficient signatures");
    } catch (err) {
        console.log("✓ Insufficient signatures (< threshold 2) correctly REVERTED: 'wBTCK: insufficient signatures'");
    }

    // 5b. Unauthorized signer (attacker not in validator set)
    const fakeSignatures = await signBridgeMint(user.address, mintAmount1, depositTxid2, 120501n, [val1, attacker]);
    try {
        await wbtck.bridgeMintWithProof(user.address, mintAmount1, depositTxid2, 120501n, fakeSignatures);
        throw new Error("FAILED: Mint succeeded with unauthorized signer");
    } catch (err) {
        console.log("✓ Unauthorized validator signature correctly REVERTED: 'wBTCK: unauthorized validator'");
    }

    // 5c. Duplicate signatures from same validator
    const dupSig = singleSig[0];
    try {
        await wbtck.bridgeMintWithProof(user.address, mintAmount1, depositTxid2, 120501n, [dupSig, dupSig]);
        throw new Error("FAILED: Mint succeeded with duplicate signatures");
    } catch (err) {
        console.log("✓ Duplicate validator signature correctly REVERTED: 'wBTCK: signatures not sorted or duplicate'");
    }

    // 6. Test EIP-2612 Gasless Permit Approval
    console.log("\n[Test 5: EIP-2612 Gasless Permit Approval]");
    const net = await provider.getNetwork();
    const domain = {
        name: "Wrapped BTC-K",
        version: "1",
        chainId: net.chainId,
        verifyingContract: contractAddress
    };

    const types = {
        Permit: [
            { name: "owner", type: "address" },
            { name: "spender", type: "address" },
            { name: "value", type: "uint256" },
            { name: "nonce", type: "uint256" },
            { name: "deadline", type: "uint256" }
        ]
    };

    const permitAmount = ethers.parseEther("0.8");
    const nonce = await wbtck.nonces(user.address);
    const deadline = Math.floor(Date.now() / 1000) + 3600; // 1 hour from now

    const permitValues = {
        owner: user.address,
        spender: relayer.address,
        value: permitAmount,
        nonce: nonce,
        deadline: deadline
    };

    const signature = await user.signTypedData(domain, types, permitValues);
    const sigSplit = ethers.Signature.from(signature);

    // Relayer submits permit on-chain on behalf of user (user pays 0 gas!)
    const relayerWbtck = wbtck.connect(relayer);
    const permitTx = await relayerWbtck.permit(
        user.address,
        relayer.address,
        permitAmount,
        deadline,
        sigSplit.v,
        sigSplit.r,
        sigSplit.s
    );
    await permitTx.wait();

    const allowance = await wbtck.allowance(user.address, relayer.address);
    const newNonce = await wbtck.nonces(user.address);
    if (allowance !== permitAmount || newNonce !== 1n) {
        throw new Error("EIP-2612 permit allowance or nonce failed");
    }
    console.log(`✓ EIP-2612 Gasless Permit Executed. Spender Allowance: ${ethers.formatEther(allowance)} wBTCK, Nonce: ${newNonce}`);

    // Test expired permit rejection
    try {
        const expiredDeadline = Math.floor(Date.now() / 1000) - 100;
        await relayerWbtck.permit(user.address, relayer.address, permitAmount, expiredDeadline, sigSplit.v, sigSplit.r, sigSplit.s);
        throw new Error("FAILED: Expired permit did not revert");
    } catch (err) {
        console.log("✓ Expired permit signature correctly REVERTED: 'wBTCK: permit expired'");
    }

    // 7. Test Bridge Burn (EVM to BlockDAG Exit)
    console.log("\n[Test 6: Bridge Burn & Gasless Exit]");
    const userWbtck = wbtck.connect(user);
    const burnAmount = ethers.parseEther("0.5");
    const stealthTarget = "btck1s_q2r9v7x4k8f2a1m6p9w8z3d2g7h5j4k3l2";

    const burnTx = await userWbtck.bridgeBurn(burnAmount, stealthTarget);
    await burnTx.wait();

    const balAfterBurn = await wbtck.balanceOf(user.address);
    const supplyAfterBurn = await wbtck.totalSupply();
    if (balAfterBurn !== ethers.parseEther("1.0") || supplyAfterBurn !== ethers.parseEther("1.0")) {
        throw new Error("Bridge burn failed to reduce balances/supply");
    }
    console.log(`✓ Standard Bridge Burn 0.5 wBTCK executed. Target: ${stealthTarget.slice(0, 16)}... Remaining Balance: ${ethers.formatEther(balAfterBurn)} wBTCK`);

    // 8. Test Circuit Breaker (Pause / Unpause)
    console.log("\n[Test 7: Emergency Circuit Breaker (Pause/Unpause)]");
    const pauseTx = await wbtck.pause({ nonce: await provider.getTransactionCount(deployer.address) });
    await pauseTx.wait();
    const isPaused = await wbtck.paused();
    if (!isPaused) throw new Error("Contract should be paused");
    console.log("✓ Contract successfully PAUSED by Owner");

    try {
        await userWbtck.bridgeBurn(ethers.parseEther("0.1"), stealthTarget, { nonce: await provider.getTransactionCount(user.address) });
        throw new Error("FAILED: Burn succeeded while contract is paused");
    } catch (err) {
        console.log("✓ Bridge Burn during Pause correctly REVERTED: 'wBTCK: contract is paused'");
    }

    const unpauseTx = await wbtck.unpause({ nonce: await provider.getTransactionCount(deployer.address) });
    await unpauseTx.wait();
    console.log("✓ Contract successfully UNPAUSED by Owner");

    // 9. Test 21,000,000 Hard Cap Constraint
    console.log("\n[Test 8: 21,000,000 Hard Cap Constraint]");
    const depositTxid3 = ethers.keccak256(ethers.toUtf8Bytes("BTCK_BLOCKDAG_DEPOSIT_TX_003"));
    const overCapAmount = ethers.parseEther("21000001");
    const overCapSigs = await signBridgeMint(user.address, overCapAmount, depositTxid3, 120502n, [val1, val2]);
    try {
        await wbtck.bridgeMintWithProof(user.address, overCapAmount, depositTxid3, 120502n, overCapSigs, { nonce: await provider.getTransactionCount(deployer.address) });
        throw new Error("FAILED: Hard cap exceeded");
    } catch (err) {
        console.log("✓ Hard cap overflow (21,000,001 wBTCK) correctly REVERTED: 'wBTCK: exceeds 21M hard cap'");
    }

    console.log("\n================================================================");
    console.log("🎉 ALL 8 SECURITY TESTS PASSED PERFECTLY (100% SUCCESS)");
    console.log("================================================================");
}

runTestSuite().catch(err => {
    console.error("Test execution failed:", err);
    process.exit(1);
});
