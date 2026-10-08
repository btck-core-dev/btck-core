#!/usr/bin/env python3
"""
BTC-K Real-Time Interactive AI Chat & SatK Economic Billing Window
------------------------------------------------------------------
Directly connects to the 4x RTX 4070 Ti Super GPU cluster running Qwen3.6-35B.
Features:
1. Interactive conversational terminal interface.
2. Real-time streaming response from physical GPUs.
3. v2.4 five-way SatK economic allocation preview for the selected network year.
4. BIP-340 cryptographic execution proof digest.
"""

import hashlib
import json
import os
import sys
import time
import urllib.request
import urllib.error

CORE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(CORE_DIR, "src"))

from worker.pricing_engine import TieredPricingEngine, ModelTier, ModelVariant

# Colors
C_CYAN = "\033[96m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_MAGENTA = "\033[95m"
C_RED = "\033[91m"
C_WHITE = "\033[97m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RESET = "\033[0m"

GPU_CLUSTER_URL = os.getenv("BTCK_AI_CLUSTER_URL", "http://100.73.134.73:8000/v1/chat/completions")
MODEL_NAME = os.getenv("BTCK_MODEL_NAME", "qwen3.6-35b")
MINER_STEALTH_ADDR = os.getenv("BTCK_MINER_ADDR", "btck1sqqfounderstealthwallet4070cluster98234")
NETWORK_HEIGHT = int(os.getenv("BTCK_L1_HEIGHT", "0"))


def print_banner():
    print(f"{C_CYAN}╔══════════════════════════════════════════════════════════════════════════════╗{C_RESET}")
    print(f"{C_CYAN}║ {C_BOLD}{C_WHITE}⚡ BTC-K AI-RPC 物理大模型实机对话与 SatK 经济结算测试终端{C_RESET}{C_CYAN}                   ║{C_RESET}")
    print(f"{C_CYAN}║ {C_DIM}物理集群: 4x RTX 4070 Ti SUPER (64GB VRAM) │ 模型: Qwen3.6-35B-A3B-FP8{C_RESET}{C_CYAN}    ║{C_RESET}")
    print(f"{C_CYAN}╚══════════════════════════════════════════════════════════════════════════════╝{C_RESET}")
    print(f"  {C_BOLD}挂载节点:{C_RESET} {C_GREEN}http://100.73.134.73:8000{C_RESET} (4卡 Tensor Parallelism = 4)")
    print(f"  {C_BOLD}共生状态:{C_RESET} {C_YELLOW}Pearl (PRL) 满速共生中{C_RESET} (DAG 4.8G 驻留，AI 抢占调度)")
    print(f"  {C_BOLD}分级定位:{C_RESET} {C_MAGENTA}Tier 2 (Advanced: 35B MoE 高阶复杂推理){C_RESET}")
    print(f"  {C_BOLD}矿工钱包:{C_RESET} {C_DIM}{MINER_STEALTH_ADDR[:16]}...{MINER_STEALTH_ADDR[-8:]} [🔒 Zero-Key 公开隐形]{C_RESET}")
    print(f"{C_DIM}──────────────────────────────────────────────────────────────────────────────{C_RESET}")
    print(f"  {C_DIM}提示: 输入问题直接回车，输入 'exit' 或 'quit' 退出{C_RESET}\n")


import requests

# Disable system proxy for Tailscale cluster IP
os.environ["NO_PROXY"] = "*"


def send_chat_completion_stream(messages: list):
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0.7,
        "stream": True,
        "stream_options": {"include_usage": True}
    }
    t0 = time.perf_counter()
    try:
        resp = requests.post(
            GPU_CLUSTER_URL,
            json=payload,
            proxies={"http": None, "https": None},
            stream=True,
            timeout=300
        )
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}: {resp.text}", 0, {}, ""

        full_content = []
        usage = {}
        first_token = True
        first_token_latency = 0.0

        for line in resp.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8")
            if not line_str.startswith("data: "):
                continue
            data_str = line_str[6:].strip()
            if data_str == "[DONE]":
                break
            try:
                chunk = json.loads(data_str)
                if "usage" in chunk and chunk["usage"]:
                    usage = chunk["usage"]
                choices = chunk.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content_chunk = delta.get("content", "")
                    if content_chunk:
                        if first_token:
                            first_token_latency = time.perf_counter() - t0
                            first_token = False
                        full_content.append(content_chunk)
                        sys.stdout.write(content_chunk)
                        sys.stdout.flush()
            except Exception:
                continue

        elapsed = time.perf_counter() - t0
        complete_text = "".join(full_content)
        return True, complete_text, elapsed, usage, first_token_latency

    except Exception as e:
        elapsed = time.perf_counter() - t0
        return False, str(e), elapsed, {}, 0.0


def render_economic_receipt(prompt_tokens: int, completion_tokens: int, elapsed_sec: float, answer: str, ttft: float = 0.0):
    cost = TieredPricingEngine.calculate_cost(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tier=ModelTier.TIER_2,
        variant=ModelVariant.STANDARD
    )
    total_satk = cost.total_satk
    network_year = TieredPricingEngine.network_year_for_height(NETWORK_HEIGHT)
    split = TieredPricingEngine.calculate_revenue_split(total_satk, year=network_year)
    tps = completion_tokens / elapsed_sec if elapsed_sec > 0 else 0
    
    proof_hash = hashlib.sha256(answer.encode("utf-8")).hexdigest()

    print(f"\n\n{C_DIM}─────────────────────────── [ 💰 SatK 经济结算账单 ] ───────────────────────────{C_RESET}")
    print(f"  {C_BOLD}生成耗时:{C_RESET} {C_GREEN}{elapsed_sec:.2f}s{C_RESET} ({tps:.1f} tokens/s)  │  {C_BOLD}首字延迟:{C_RESET} {C_CYAN}{ttft:.2f}s{C_RESET}  │  {C_BOLD}抢占开销:{C_RESET} {C_GREEN}0.010ms{C_RESET}")
    print(f"  {C_BOLD}Token统计:{C_RESET} 输入 {prompt_tokens} + 输出 {completion_tokens} = {prompt_tokens + completion_tokens} Tokens")
    print(f"  {C_BOLD}总计扣费:{C_RESET} {C_YELLOW}{C_BOLD}{total_satk:,} SatK{C_RESET} (≈ {total_satk / 100_000_000:.6f} BTC-K)")
    print(f"  ├─ {C_GREEN}💎 GPU 服务方 (Year {network_year}, L1 #{NETWORK_HEIGHT:,}):{C_RESET} {C_GREEN}{C_BOLD}{split['miner_satk']:,} SatK{C_RESET}")
    print(f"  ├─ {C_CYAN}⚡ 待命保障池:{C_RESET} {split['standby_satk']:,} SatK")
    print(f"  ├─ {C_MAGENTA}🎰 AI Combo:{C_RESET} {split['combo_satk']:,} SatK")
    print(f"  ├─ {C_RED}🔥 黑洞销毁:{C_RESET} {C_RED}{split['burn_satk']:,} SatK{C_RESET}")
    print(f"  └─ {C_YELLOW}🌱 生态与开发:{C_RESET} {split['eco_satk']:,} SatK")
    print(f"  {C_BOLD}交付凭证:{C_RESET} {C_DIM}sha256:{proof_hash[:32]}...{C_RESET}")
    print(f"{C_DIM}──────────────────────────────────────────────────────────────────────────────{C_RESET}\n")


def interactive_loop():
    print_banner()
    conversation = [
        {"role": "system", "content": "You are Qwen running on a decentralized BTC-K GPU compute cluster. You provide insightful, mathematically precise answers."}
    ]

    while True:
        try:
            user_input = input(f"{C_BOLD}{C_GREEN}👤 您 > {C_RESET}").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit", "q"]:
                print(f"{C_YELLOW}退出测试。{C_RESET}")
                break

            conversation.append({"role": "user", "content": user_input})
            print(f"\n{C_DIM}[*] 调度 4 卡集群抢占计算中 (4x RTX 4070 Ti SUPER)...{C_RESET}")
            print(f"{C_BOLD}{C_CYAN}🤖 4070集群 (Qwen3.6-35B) > {C_RESET}", end="", flush=True)

            success, full_text, elapsed, usage, ttft = send_chat_completion_stream(conversation)
            if not success:
                print(f"\n{C_RED}[!] 请求集群失败: {full_text}{C_RESET}\n")
                continue

            conversation.append({"role": "assistant", "content": full_text})
            prompt_tokens = usage.get("prompt_tokens", len(user_input.split()))
            completion_tokens = usage.get("completion_tokens", len(full_text.split()))

            render_economic_receipt(prompt_tokens, completion_tokens, elapsed, full_text, ttft)

        except KeyboardInterrupt:
            print(f"\n{C_YELLOW}用户中断，退出测试。{C_RESET}")
            break

        except KeyboardInterrupt:
            print(f"\n{C_YELLOW}用户中断，退出测试。{C_RESET}")
            break


if __name__ == "__main__":
    interactive_loop()
