# BTC-K Compute Node: resident model and Pearl pause

This software uses development credits. The invitation preview gateway is https://compute.bitcoin-k.com. Miners and customers do not need their own domain, public IP or inbound port forwarding. Miner software connects outward over HTTPS; the local model remains on localhost.

## One program on Linux

1. Extract this package. Prepare your own Pearl miner and local model runtime/weights.
2. Copy docs/examples/compute-node.example.json to a private configuration outside the extracted directory. Set your Worker invitation credential, model IDs, local endpoint, GPU indices, miner arguments (including your own pool and Pearl wallet), and the model startup command. All launch commands are argument lists; no remote shell execution is added.
3. Run: sudo python3 scripts/l2_compute_node.py --config /absolute/private/node.json

The program starts the configured miner and model when absent, or adopts existing processes. The model stays resident in GPU memory. A claimed inference request pauses the exact miner process, executes the local model, then resumes mining. Each inference round releases mining before the next round. It does not unload the model or stop/restart Pearl per request. Graceful disconnect leaves the miner and model running.

A separate process inside this same software holds a bounded pause lease. It checks PID birth identity, resumes on owner death, and resumes on timeout even if the calling process cannot run finally cleanup. The default inference timeout is90 seconds, pause limit120 seconds; the pause limit is at most180 seconds. Root is needed when the existing miner runs as root. CPU suspension keeps miner GPU allocations; choose a model/KV configuration that fits alongside those allocations.

Optional one-step Linux service installation: sudo python3 deploy/install_compute_node.py --config /absolute/private/node.json --start. This installs a root service and enables boot startup; it refuses to overwrite an existing installation. The installed4070 service adopts the existing Pearl system service and starts the existing local35B model; no model download is performed.

## One program on Windows

Run the same single executable with a private Windows node configuration:

BTC-K-Compute-Connect.exe --node-config C:\private\node.json

Use Windows paths for your local miner/model executables, logs, lease and status files; set miner_process_name to SRBMiner-MULTI.exe. Launch as administrator when the miner requires elevated access. This starts the configured miner/model if absent, then accepts work. Diagnostics go to compute-node.log beside your private config. --check validates configuration without starting processes. The Linux real-GPU test does not qualify Windows GPU inference.

For an existing Windows scheduled mining task, set `miner_task` to its exact task name instead of supplying a second miner command. The node adopts an existing miner first; it starts the task only when the miner is absent. A miner's own restart mechanism and its external supervisor must also cooperate to prevent duplicate mining processes. Multiple matching miner processes close admission rather than selecting an arbitrary process.

For automatic startup, open an elevated PowerShell in the Windows package directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\install_compute_node_windows.ps1 -ConfigPath C:\private\node.json -ExecutablePath .\BTC-K-Compute-Connect.exe -Start
```

This installs the standalone executable with the current administrator's identity, highest privileges and automatic restart. The Compute Node executable does not require a separate Python installation. Your chosen local model runtime still needs its own dependencies. The installer refuses to overwrite an existing installation.

The source package also supports an existing Python installation:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\install_compute_node_windows.ps1 -ConfigPath C:\private\node.json -PythonPath "C:\Program Files\Python310\python.exe" -Start
```

The source option requires Python 3.10+ and copies the node into a protected directory. The selected local runtime may be launched through `wsl.exe` using an explicit argument list; prepare the WSL distribution, runtime and weights first. Neither this installer nor the executable downloads models or installs vLLM. Service startup verification does not replace a real computer reboot test.

For a runtime whose startup kernels require exclusive GPU time, optional `model_startup_pause_marker` pauses the identified miner after that exact message appears in the new private model log. This requires an explicit local `model_stop_command`, a startup timeout of30–900 seconds, and a pause of30–180 seconds (`model_startup_timeout`, `model_startup_pause_seconds`). A ready model remains resident and mining resumes. A failed warmup stops the selected model before releasing the mining lease. A failed-startup marker beside the configuration prevents repeated automatic GPU launches; inspect the private log, correct the runtime, then remove that marker to retry. This startup protection needs its own hardware validation and does not qualify a runtime for long contexts.

## Invitation quick start

Download from https://compute.bitcoin-k.com/downloads/. Open Compute Connect and import the operator-issued private Worker invitation JSON; the software redeems it over HTTPS and preserves a durable retry identity. Start or select your local model/Pearl configuration, choose GPUs, and connect. A Worker invitation does not grant customer access. Local model IDs are mapped to approved network model IDs; arbitrary models are not automatically added to the shared catalog.

## Desktop connection

Windows opens BTC-K-Compute-Connect.exe; Linux GUI uses bash deploy/compute-connect.sh (Python3.10+,Tk,NVIDIA tools). Start your model/miner first, import your private Worker profile, detect/select model and GPU, then choose Pause Pearl during requests. Use matching administrator/root rights if required by the miner. Reasoning is a backend option; select Disabled for the tested Qwen endpoint.

Run alongside Pearl remains an optional mode and uses the mining-rate baseline and transient allowance. Pause mode deliberately ignores the mining hashrate API during suspension, while keeping miner identity, free-memory and GPU-temperature checks.

Agent tools run on the customer's computer, not the GPU node. Model selection/download/runtime belong to the GPU operator. Windows process-control code requires separate Windows acceptance; Linux real-GPU results must not be reported as Windows GPU validation. No real BTC-K deposits, withdrawals or chain settlement is enabled.


## Model recommendations and manual choices

Open **GPU / 模型推荐**. Select your local model directory (or a folder containing model directories) and the Python executable in your existing vLLM environment, then Detect. The picker measures each NVIDIA GPU's total/free VRAM and temperature. Pearl and any resident model remain allocated; their memory is not assumed reclaimable. Select GPUs, use the recommended local model/context, or choose model, context, KV precision, prefill chunk and speculative decoding manually. Save the selection, then Connect. This saves an actual model launch command and starts it if absent. Import your own miner launch settings in your private node profile, or start the miner first.

Recommendations currently inspect complete local Hugging Face safetensors models. GGUF, remote catalogs and automatic downloads are not implemented by this picker. A detected tool template is a candidate, not proof of Agent qualification. The default recommendation uses backend-default KV precision, chunked prefill, one request and no speculation. Prefix caching is selected separately: unknown hybrid-model combinations default to disabled for compatibility. Known invalid prefix-cache/prefill combinations are rejected before launch. FP8/4bit KV and n-gram speculation are optional and need engine/hardware/accuracy validation. A configuration listing an option does not prove that the installed runtime supports it. Native Windows vLLM GPU execution is not qualified; an existing localhost model endpoint remains supported. Do not select an unrelated Windows Python installation expecting it to provide vLLM.

Capacity estimates include weight overhead, workspace, hybrid state allowance and a reserve on every GPU, and account for replicated KV heads. They are estimates, not measured throughput or maximum context guarantees. The picker never stops an existing model: selected launch settings take effect on its next start. Current network model limits and prices still come from the approved gateway catalog; selecting a longer local context does not silently change that catalog. A short pause lease may limit a long-context request even when its memory fits.

Without a desktop, run `python3 scripts/l2_model_advisor.py --models /your/local/model-library --gpus 0,1,2,3`. Optional `--model DIRECTORY_NAME --context 16384 --kv auto --prefill 256` selects a manual plan. With `--python /your/vllm/python --node-config /private/existing.json --save-config /private/new.json`, save a new private configuration using the selected startup plan. Existing configurations are never overwritten. Both tools inspect metadata without loading model weights or making inference requests.

## KCU pricing

The desktop **计费说明** panel displays the connected gateway's model rate in **KCU per 1,000 weighted units**. The customer page shows model KCU rates and each completed task's actual KCU, with development SatK settlement as secondary information. Weighted units are input tokens plus ceil(1.5 × output tokens). Configured context capacity is not prepaid token usage; cache hits currently have no separate discount. Model recommendations do not change gateway prices.

The authoritative policy is tokenomics_v2_canonical.md sections8.1 and8.5, together with pricing_engine.py and inference_pilot.py. The older memory matrix's fixed SatK examples are not current pricing. This preview still uses development credits, integer SatK settlement and a fixed bootstrap reference; it does not accept mainnet deposits or provide withdrawals. Uncertain outcomes retain their original budget for reconciliation. Software service status is shown without exposing validation internals.


Reference measurements are matched against the selected runtime version/code, model metadata and GPU configuration. A reference-machine result is shown as such; it does not mark another computer as already qualified. Fresh free-memory checks and per-GPU reserves still apply. If a measured profile supplies a memory observation, the estimate includes5% extra headroom plus the per-GPU reserve and preserves its validated GPU-memory fraction. Manual changes to cache mode do not inherit a different configuration's qualification. The GUI shows elapsed waiting time while a managed local model initializes.

Gateway operators can set `max_concurrent_requests` to an integer from1 to32 in the private gateway configuration. The default remains1. A fleet with multiple workers needs enough gateway slots to serve those workers simultaneously; each worker still accepts one request at a time. Reservations and settlement remain atomic, and uncertain tasks keep their existing holds. Increasing this limit does not increase a worker's model capacity.
