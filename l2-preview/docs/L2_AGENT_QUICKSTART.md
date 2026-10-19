# BTC-K L2 Agent kit: development-credit preview

Use Python 3.10+; the included signed-inference client needs no third-party Python packages. The invitation preview gateway is https://compute.bitcoin-k.com. Obtain a private Agent profile from the operator; Worker and Agent credentials are separate.

1. Obtain a private Agent profile from the preview operator. It contains `gateway_url`, a dedicated Agent `token`, and the trusted public receipt key. A Worker invitation/token is a different credential.
2. Keep that profile outside this extracted package and restrict access to your user. The example contains placeholders only. Do not accept a receipt key from an untrusted inference response.
3. List currently available models with the client:

```python
import json, sys
sys.path[:0] = ['src', 'sdk']
from btck_agent.inference_pilot import PilotClient
with open('/private/agent.json', encoding='utf-8') as f:
    cfg = json.load(f)
client = PilotClient(cfg['gateway_url'], cfg['token'], cfg['trusted_receipt_pubkey'])
print(client.models())
```

Select an available model with tool routing enabled. Read its `tool_call_qualification` and output limit; an enabled route is not a general model capability certificate.

4. From this extracted directory, run the included read-only tool example with your private profile:

```sh
python3 scripts/l2_tool_agent.py --config /private/agent.json \
  --model APPROVED_ONLINE_MODEL --prompt-file docs/examples/l2-preview/task.txt \
  --services docs/examples/l2-preview/services.json --run-id unique-task-001 \
  --journal /private/task-001.json --budget 1000000 --max-tokens 256
```

The first round explicitly requires a tool call. `lookup_service` runs in your Agent process; the GPU node receives the model request and returns structured calls. The tool result is then sent to the model for the second round. No shell or arbitrary-file tool is enabled.

The client verifies the pinned receipt signer and full request/message binding. The budget applies to the complete tool loop. Keep your run ID, prompt, profile, budget and journal stable when resuming. Re-running an identical completed task returns its saved signed results without new inference or charges. A tool interrupted during execution remains uncertain and is not automatically repeated. A truncated or UNKNOWN inference is not automatically repaired with another task ID.

The CLI reports measured KCU usage first, with development SatK settlement as secondary information. These are not real BTC-K deposits, withdrawals or on-chain settlement. Wallet-related dependency code included for signature verification does not enable real funds in this preview.


The client sends UTF-8 JSON and waits up to135 seconds by default, covering the bounded gateway/Worker request period. The tool CLI reads the approved model byte limit, keeps4KiB for tool-message overhead, and caps its prompt file at60,000 bytes. The local service catalog remains limited to16KiB. All message history and tool definitions still share a64KiB protocol ceiling; token context is checked separately by the model backend. Document-summary helpers retain their existing12,000-byte document bound. Larger context does not mean that configured capacity is billed in advance: measured input and output determine KCU usage. Choose a sufficient explicit budget for long prompts, and keep it stable for request recovery.
