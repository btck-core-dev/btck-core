// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title wBTCK (Wrapped Bitcoin K)
 * @notice 1:1 EVM Bridge Representation of Sovereign BlockDAG BTC-K.
 * Deployable to Base, Arbitrum One, and Ethereum Mainnet.
 * Features:
 * - ERC-20 Standard (18 decimals, 21,000,000 wBTCK hard cap invariant)
 * - EIP-2612 Gasless Permit Approval (DOMAIN_SEPARATOR, nonces, ecrecover)
 * - Cryptographic Multi-Sig Proof-of-Lock (M-of-N threshold relayers)
 * - Double-Spend & Replay Protection (processedBtckTxs tracking)
 * - Gasless Bridge Exit (bridgeBurnWithPermit)
 * - Circuit Breaker (Pausable emergency stop)
 */

interface IERC20 {
    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);
    function totalSupply() external view returns (uint256);
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 value) external returns (bool);
    function allowance(address owner, address spender) external view returns (uint256);
    function approve(address spender, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

interface IERC20Permit {
    function permit(
        address owner,
        address spender,
        uint256 value,
        uint256 deadline,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external;
    function nonces(address owner) external view returns (uint256);
    function DOMAIN_SEPARATOR() external view returns (bytes32);
}

contract wBTCK is IERC20, IERC20Permit {
    string public constant name = "Wrapped BTC-K";
    string public constant symbol = "wBTCK";
    uint8 public constant decimals = 18;
    uint256 public constant MAX_SUPPLY = 21_000_000 * 1e18; // 21M hard cap invariant

    uint256 private _totalSupply;
    mapping(address => uint256) private _balances;
    mapping(address => mapping(address => uint256)) private _allowances;

    address public owner;
    bool public paused;

    // --- EIP-2612 Permit State ---
    bytes32 public immutable override DOMAIN_SEPARATOR;
    bytes32 public constant PERMIT_TYPEHASH =
        keccak256("Permit(address owner,address spender,uint256 value,uint256 nonce,uint256 deadline)");
    mapping(address => uint256) public override nonces;

    // --- Multi-Sig Proof-of-Lock Bridge State ---
    bytes32 public constant BRIDGE_MINT_TYPEHASH =
        keccak256("BridgeMint(address to,uint256 amount,bytes32 btckTxid,uint256 blockHeight,uint256 chainId,address verifyingContract)");

    mapping(address => bool) public isValidator;
    uint256 public validatorCount;
    uint256 public validatorThreshold;

    // Anti-Replay Protection: BTC-K transaction id => bridged status
    mapping(bytes32 => bool) public processedBtckTxs;

    // --- Events ---
    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);
    event Paused(address account);
    event Unpaused(address account);
    event ValidatorAdded(address indexed validator);
    event ValidatorRemoved(address indexed validator);
    event ValidatorThresholdChanged(uint256 oldThreshold, uint256 newThreshold);
    event BridgeDepositMinted(address indexed recipient, uint256 amount, bytes32 indexed btckTxid, uint256 blockHeight);
    event BridgeWithdrawalBurned(address indexed sender, uint256 amount, string recipientStealthAddr);

    modifier onlyOwner() {
        require(msg.sender == owner, "wBTCK: caller is not owner");
        _;
    }

    modifier whenNotPaused() {
        require(!paused, "wBTCK: contract is paused");
        _;
    }

    modifier whenPaused() {
        require(paused, "wBTCK: contract is not paused");
        _;
    }

    constructor(address[] memory initialValidators, uint256 initialThreshold) {
        owner = msg.sender;
        emit OwnershipTransferred(address(0), msg.sender);

        DOMAIN_SEPARATOR = keccak256(
            abi.encode(
                keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
                keccak256(bytes(name)),
                keccak256(bytes("1")),
                block.chainid,
                address(this)
            )
        );

        if (initialValidators.length > 0) {
            require(initialThreshold > 0 && initialThreshold <= initialValidators.length, "wBTCK: invalid threshold");
            for (uint256 i = 0; i < initialValidators.length; i++) {
                address val = initialValidators[i];
                require(val != address(0), "wBTCK: zero validator address");
                require(!isValidator[val], "wBTCK: duplicate validator");
                isValidator[val] = true;
                emit ValidatorAdded(val);
            }
            validatorCount = initialValidators.length;
            validatorThreshold = initialThreshold;
            emit ValidatorThresholdChanged(0, initialThreshold);
        }
    }

    // ==========================================
    // ERC-20 Standard Implementation
    // ==========================================

    function totalSupply() public view override returns (uint256) {
        return _totalSupply;
    }

    function balanceOf(address account) public view override returns (uint256) {
        return _balances[account];
    }

    function transfer(address to, uint256 value) public override returns (bool) {
        _transfer(msg.sender, to, value);
        return true;
    }

    function allowance(address accountOwner, address spender) public view override returns (uint256) {
        return _allowances[accountOwner][spender];
    }

    function approve(address spender, uint256 value) public override returns (bool) {
        _approve(msg.sender, spender, value);
        return true;
    }

    function transferFrom(address from, address to, uint256 value) public override returns (bool) {
        _spendAllowance(from, msg.sender, value);
        _transfer(from, to, value);
        return true;
    }

    // ==========================================
    // EIP-2612 Gasless Permit Implementation
    // ==========================================

    function permit(
        address tokenOwner,
        address spender,
        uint256 value,
        uint256 deadline,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) public override {
        require(block.timestamp <= deadline, "wBTCK: permit expired");

        bytes32 structHash = keccak256(
            abi.encode(PERMIT_TYPEHASH, tokenOwner, spender, value, nonces[tokenOwner]++, deadline)
        );

        bytes32 hash = keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, structHash));
        address signer = ecrecover(hash, v, r, s);

        require(signer != address(0) && signer == tokenOwner, "wBTCK: invalid permit signature");
        _approve(tokenOwner, spender, value);
    }

    // ==========================================
    // Multi-Sig Proof-of-Lock Bridge Operations
    // ==========================================

    /**
     * @notice Bridge Mint with cryptographic multi-signature validation.
     * @dev Signatures must be sorted in ascending order of signer address to prevent duplicate voting.
     * @param to Destination EVM address receiving wBTCK
     * @param amount Token amount to mint (in satoshi wei, 1 BTC-K = 1e18 wBTCK)
     * @param btckTxid Unique BlockDAG deposit transaction hash
     * @param blockHeight BlockDAG block height where deposit confirmed
     * @param signatures Array of packed signatures (65 bytes each: r[32] + s[32] + v[1])
     */
    function bridgeMintWithProof(
        address to,
        uint256 amount,
        bytes32 btckTxid,
        uint256 blockHeight,
        bytes[] calldata signatures
    ) external whenNotPaused {
        require(to != address(0), "wBTCK: mint to zero address");
        require(amount > 0, "wBTCK: zero mint amount");
        require(!processedBtckTxs[btckTxid], "wBTCK: tx already processed");
        require(_totalSupply + amount <= MAX_SUPPLY, "wBTCK: exceeds 21M hard cap");
        require(validatorThreshold > 0, "wBTCK: validator threshold not set");
        require(signatures.length >= validatorThreshold, "wBTCK: insufficient signatures");

        // Compute EIP-712 typed data digest
        bytes32 messageHash = keccak256(
            abi.encode(BRIDGE_MINT_TYPEHASH, to, amount, btckTxid, blockHeight, block.chainid, address(this))
        );
        bytes32 ethSignedDigest = keccak256(
            abi.encodePacked("\x19Ethereum Signed Message:\n32", messageHash)
        );

        // Verify multi-signatures and ensure strictly increasing addresses to reject duplicates
        address lastSigner = address(0);
        for (uint256 i = 0; i < signatures.length; i++) {
            require(signatures[i].length == 65, "wBTCK: invalid signature length");
            bytes memory sig = signatures[i];
            bytes32 r;
            bytes32 s;
            uint8 v;
            assembly {
                r := mload(add(sig, 32))
                s := mload(add(sig, 64))
                v := byte(0, mload(add(sig, 96)))
            }

            address signer = ecrecover(ethSignedDigest, v, r, s);
            require(signer != address(0), "wBTCK: ecrecover failed");
            require(isValidator[signer], "wBTCK: unauthorized validator");
            require(signer > lastSigner, "wBTCK: signatures not sorted or duplicate");
            lastSigner = signer;
        }

        // Mark transaction as bridged (Replay Protection)
        processedBtckTxs[btckTxid] = true;

        // Mint wBTCK to recipient
        _totalSupply += amount;
        _balances[to] += amount;

        emit Transfer(address(0), to, amount);
        emit BridgeDepositMinted(to, amount, btckTxid, blockHeight);
    }

    /**
     * @notice Standard Bridge Burn: Burns wBTCK on EVM to release native BTC-K on BlockDAG stealth address.
     */
    function bridgeBurn(uint256 amount, string calldata recipientStealthAddr) public whenNotPaused {
        require(amount > 0, "wBTCK: zero burn amount");
        require(_balances[msg.sender] >= amount, "wBTCK: insufficient balance");
        require(bytes(recipientStealthAddr).length >= 10, "wBTCK: invalid stealth address");

        _balances[msg.sender] -= amount;
        _totalSupply -= amount;

        emit Transfer(msg.sender, address(0), amount);
        emit BridgeWithdrawalBurned(msg.sender, amount, recipientStealthAddr);
    }

    /**
     * @notice Gasless Bridge Burn: Approves via EIP-2612 permit and burns in a single atomic transaction.
     */
    function bridgeBurnWithPermit(
        address account,
        uint256 amount,
        string calldata recipientStealthAddr,
        uint256 deadline,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external whenNotPaused {
        permit(account, msg.sender, amount, deadline, v, r, s);
        _spendAllowance(account, msg.sender, amount);

        require(amount > 0, "wBTCK: zero burn amount");
        require(_balances[account] >= amount, "wBTCK: insufficient balance");
        require(bytes(recipientStealthAddr).length >= 10, "wBTCK: invalid stealth address");

        _balances[account] -= amount;
        _totalSupply -= amount;

        emit Transfer(account, address(0), amount);
        emit BridgeWithdrawalBurned(account, amount, recipientStealthAddr);
    }

    // ==========================================
    // Administrative & Circuit Breaker
    // ==========================================

    function pause() external onlyOwner whenNotPaused {
        paused = true;
        emit Paused(msg.sender);
    }

    function unpause() external onlyOwner whenPaused {
        paused = false;
        emit Unpaused(msg.sender);
    }

    function addValidator(address validator) external onlyOwner {
        require(validator != address(0), "wBTCK: zero address");
        require(!isValidator[validator], "wBTCK: validator already registered");
        isValidator[validator] = true;
        validatorCount++;
        emit ValidatorAdded(validator);
    }

    function removeValidator(address validator) external onlyOwner {
        require(isValidator[validator], "wBTCK: not a validator");
        isValidator[validator] = false;
        validatorCount--;
        require(validatorThreshold <= validatorCount, "wBTCK: threshold exceeds remaining validators");
        emit ValidatorRemoved(validator);
    }

    function setValidatorThreshold(uint256 newThreshold) external onlyOwner {
        require(newThreshold > 0 && newThreshold <= validatorCount, "wBTCK: invalid threshold");
        emit ValidatorThresholdChanged(validatorThreshold, newThreshold);
        validatorThreshold = newThreshold;
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "wBTCK: new owner is zero address");
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }

    // ==========================================
    // Internal Helpers
    // ==========================================

    function _transfer(address from, address to, uint256 value) internal {
        require(from != address(0), "wBTCK: transfer from zero");
        require(to != address(0), "wBTCK: transfer to zero");
        require(_balances[from] >= value, "wBTCK: insufficient balance");

        _balances[from] -= value;
        _balances[to] += value;
        emit Transfer(from, to, value);
    }

    function _approve(address accountOwner, address spender, uint256 value) internal {
        require(accountOwner != address(0), "wBTCK: approve from zero");
        require(spender != address(0), "wBTCK: approve to zero");
        _allowances[accountOwner][spender] = value;
        emit Approval(accountOwner, spender, value);
    }

    function _spendAllowance(address accountOwner, address spender, uint256 value) internal {
        uint256 currentAllowance = allowance(accountOwner, spender);
        if (currentAllowance != type(uint256).max) {
            require(currentAllowance >= value, "wBTCK: insufficient allowance");
            _approve(accountOwner, spender, currentAllowance - value);
        }
    }
}
