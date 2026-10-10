"""
BTC-K Drop-in OpenAI API Client
===============================
Allows any existing Agent (LangChain, AutoGen, CrewAI, Eliza) to switch to
BTC-K's decentralized inference network by changing only the import or base_url.

Usage:
    from btck_agent.openai_dropin import OpenAI

    client = OpenAI(base_url="https://api.btck.network/v1")
    resp = client.chat.completions.create(
        model="deepseek-r1", # or qwen3.6-35b-uncensored
        messages=[{"role": "user", "content": "Analyze smart contract vulnerability"}]
    )
    print(resp.choices[0].message.content)
"""

import json
import urllib.request
import urllib.error
from types import SimpleNamespace
from typing import List, Dict, Any, Optional

class _Completions:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def create(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        **kwargs
    ) -> SimpleNamespace:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            **kwargs
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}" if self.api_key else "Bearer satk-faucet-trial"
        }
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            raise RuntimeError(f"BTC-K Inference Gateway Error [{e.code}]: {err_body}")

        # Wrap in OpenAI-compatible dot-accessible object
        def dict_to_obj(d):
            if isinstance(d, dict):
                return SimpleNamespace(**{k: dict_to_obj(v) for k, v in d.items()})
            elif isinstance(d, list):
                return [dict_to_obj(v) for v in d]
            return d

        return dict_to_obj(data)

class _Chat:
    def __init__(self, base_url: str, api_key: str):
        self.completions = _Completions(base_url, api_key)

class OpenAI:
    """Drop-in replacement for openai.OpenAI"""
    def __init__(self, base_url: str = "http://127.0.0.1:8080/v1", api_key: str = "satk-faucet-trial"):
        self.base_url = base_url
        self.api_key = api_key
        self.chat = _Chat(base_url, api_key)
