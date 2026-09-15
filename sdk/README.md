# `btck-agent` Python SDK

Autonomous, Zero-KYC AI Agent Micropayments powered by **BTC-K (Kaspa-Private-Bitcoin)**.

## Key Capabilities
- **Dual-Key Stealth Privacy**: Complete anonymity for agents and wallet holders.
- **HTTP 402 Pay-per-Token**: Native RFC 7231 AI model billing in Sats (0.01 Sats / token).
- **Zero External Dependencies**: Lightweight, portable, runs in pure Python standard library.

## Quick Start

```python
from btck_agent import BTCKWallet, BTCKAgentClient

# 1. Create an anonymous Agent Stealth Wallet
wallet = BTCKWallet.generate()
print("Agent Stealth Address:", wallet.address)

# 2. Query Agent Balance
balance = wallet.get_balance()
print(f"Available Sats: {balance['total_sats']:,} Sats")

# 3. Call AI Models with Automatic Sats Micropayment
client = BTCKAgentClient(wallet=wallet, gateway_url="http://node.btck.network:8080")
response = client.chat_completion(
    model="deepseek-r1",
    prompt="Explain why AI agents prefer Sats micropayments over fiat credit cards."
)
print("AI Response:", response["choices"][0]["message"]["content"])
```
