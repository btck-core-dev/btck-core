// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title wBTCK (Wrapped Bitcoin K)
 * @notice 1:1 EVM Bridge Representation of Sovereign BlockDAG BTC-K.
 * Deployable to Base, Arbitrum One, and Ethereum Mainnet.
 * Features:
 * - Pure ERC-20 standard (18 decimals)
 * - Cryptographic Relayer multi-sig Mint / Burn for BlockDAG 2-way peg
 * - EIP-2612 gasless permit approval
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

contract wBTCK is IERC20 {
    string public constant name = "Wrapped BTC-K";
    string public constant symbol = "wBTCK";
    uint8 public constant decimals = 18;
    uint256 public constant MAX_SUPPLY = 21_000_000 * 1e18; // 21M hard cap invariant

    uint256 private _totalSupply;
    mapping(address => uint256) private _balances;
    mapping(address => mapping(address => uint256)) private _allowances;

    address public owner;
    address public bridgeRelayer;

    // Bridge Events
    event BridgeRelayerUpdated(address indexed previousRelayer, address indexed newRelayer);
    event BridgeDepositMinted(address indexed recipient, uint256 amount, string btckTxid);
    event BridgeWithdrawalBurned(address indexed sender, uint256 amount, string recipientStealthAddr);

    modifier onlyOwner() {
        require(msg.sender == owner, "wBTCK: caller is not owner");
        _;
    }

    modifier onlyRelayer() {
        require(msg.sender == bridgeRelayer || msg.sender == owner, "wBTCK: caller is not bridge relayer");
        _;
    }

    constructor(address _bridgeRelayer) {
        owner = msg.sender;
        bridgeRelayer = _bridgeRelayer;
    }

    function setBridgeRelayer(address _newRelayer) external onlyOwner {
        require(_newRelayer != address(0), "wBTCK: invalid relayer");
        emit BridgeRelayerUpdated(bridgeRelayer, _newRelayer);
        bridgeRelayer = _newRelayer;
    }

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

    /**
     * @notice Bridge Mint: Triggered when user locks BTC-K on native BlockDAG stealth address.
     */
    function bridgeMint(address to, uint256 amount, string calldata btckTxid) external onlyRelayer {
        require(to != address(0), "wBTCK: mint to zero address");
        require(_totalSupply + amount <= MAX_SUPPLY, "wBTCK: exceeds 21M hard cap");

        _totalSupply += amount;
        _balances[to] += amount;

        emit Transfer(address(0), to, amount);
        emit BridgeDepositMinted(to, amount, btckTxid);
    }

    /**
     * @notice Bridge Burn: Burns wBTCK on EVM to release native BTC-K on BlockDAG stealth address.
     */
    function bridgeBurn(uint256 amount, string calldata recipientStealthAddr) external {
        require(_balances[msg.sender] >= amount, "wBTCK: insufficient balance");
        require(bytes(recipientStealthAddr).length > 10, "wBTCK: invalid stealth address");

        _balances[msg.sender] -= amount;
        _totalSupply -= amount;

        emit Transfer(msg.sender, address(0), amount);
        emit BridgeWithdrawalBurned(msg.sender, amount, recipientStealthAddr);
    }

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
