import json
import secrets
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener


PIN = json.loads((Path(__file__).resolve().parents[1] / "config" / "model-lock.json").read_text())
ENDPOINT = f"http://127.0.0.1:{PIN['port']}"


def api(path, payload=None, timeout=120):
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(ENDPOINT + path, body, {"Content-Type": "application/json"})
    with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
        return json.load(response)


def probe():
    version = api("/api/version", timeout=4)["version"]
    installed = api("/api/tags")["models"]
    if version != PIN["runtime_version"] or not any(
        m["name"] == PIN["model"] and m["digest"] == PIN["model_digest"] for m in installed
    ):
        raise ValueError("Installed runtime or model differs from config/model-lock.json")
    case_id = "po_" + secrets.token_hex(4)
    messages = [{"role": "user", "content": f"Use the lookup tool to find the bank credit for {case_id}. Return its exact integer value in cents."}]
    lookup = {"type": "function", "function": {
        "name": "lookup_bank_credit",
        "description": "Read the posted credit for a payout, in integer cents.",
        "parameters": {"type": "object", "properties": {"payout_id": {"type": "string"}}, "required": ["payout_id"]},
    }}
    config = {"model": PIN["model"], "messages": messages, "stream": False, "think": False,
              "options": {"num_ctx": PIN["context"], "temperature": 0, "num_predict": 128}}
    started = time.monotonic()
    first = api("/api/chat", {**config, "tools": [lookup]})
    message = first["message"]
    calls = [c["function"] for c in message.get("tool_calls", [])]
    if len(calls) != 1 or calls[0]["name"] != "lookup_bank_credit" or calls[0]["arguments"] != {"payout_id": case_id}:
        raise ValueError(f"Unexpected tool selection or arguments: {calls!r}")
    amount = 10000 + secrets.randbelow(90000)
    messages.extend([message, {"role": "tool", "tool_name": "lookup_bank_credit", "content": json.dumps({"credit_cents": amount})}])
    last = api("/api/chat", {**config, "format": {
        "type": "object", "properties": {"credit_cents": {"type": "integer"}}, "required": ["credit_cents"]
    }})
    if json.loads(last["message"]["content"]) != {"credit_cents": amount}:
        raise ValueError("Model did not return the tool's exact integer amount")
    loaded = next((m for m in api("/api/ps")["models"] if m["digest"] == PIN["model_digest"]), {})
    if loaded.get("size_vram", 0) <= 0:
        raise ValueError("Ollama did not report GPU residency for the pinned model")
    return {"runtime_version": version, "model_digest": PIN["model_digest"], "context": PIN["context"],
            "gpu_resident_bytes": loaded["size_vram"], "elapsed_seconds": round(time.monotonic() - started, 3),
            "generated_tokens": first["eval_count"] + last["eval_count"], "tool_round_trip": True}
