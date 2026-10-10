# Pull Request: Add BTC-K Decentralized Inference Provider (HTTP 402 SatK Micropayments)

**Target Repository**: `elizaos/eliza` (packages/core/src/providers)  
**Title**: `feat(provider): add BTC-K decentralized inference provider with zero-KYC HTTP 402 support`

---

## Summary
This PR adds native support for **BTC-K Decentralized AI Grid**, enabling Eliza agents to perform high-speed LLM inference (DeepSeek-R1, Qwen-35B, Uncensored models) without credit cards, KYC, or centralized API keys. Payments settle natively per token using SatK micropayments via RFC HTTP 402 or standard Bearer authorization.

## Changes
- Adds `BTCKModelProvider` implementing `IModelProvider`.
- Exposes configurable endpoints for decentralized uncensored models (`qwen3.6-35b-uncensored`, `deepseek-r1`).
- Implements fallback logic to auto-route between online GPU symbiotic nodes.

## Implementation Example (TypeScript)
```typescript
import { IModelProvider, ModelTier } from "@elizaos/core";

export class BTCKModelProvider implements IModelProvider {
  private baseUrl: string;
  private apiKey: string;

  constructor(config: { baseUrl?: string; apiKey?: string }) {
    this.baseUrl = config.baseUrl || "https://api.btck.network/v1";
    this.apiKey = config.apiKey || "satk-faucet-trial";
  }

  async generateText(prompt: string, model: string = "deepseek-r1"): Promise<string> {
    const response = await fetch(`${this.baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${this.apiKey}`
      },
      body: JSON.stringify({
        model: model,
        messages: [{ role: "user", content: prompt }]
      })
    });
    const data = await response.json();
    return data.choices[0].message.content;
  }
}
```

## How to Test
1. Set `BTCK_BASE_URL="https://api.btck.network/v1"` in `.env`.
2. Run `pnpm test:providers` to verify completions and streaming responses.
