"""Minimal CUDA smoke runner; model output is advisory and never executes tools."""
import argparse
import json
import re
from pathlib import Path
from agent_evolution_protocol.coordination.model_contract import validate_proposal, safe_fallback

def main():
    p = argparse.ArgumentParser(); p.add_argument("--model-dir", required=True); a = p.parse_args()
    model = Path(a.model_dir)
    if not model.is_dir(): raise SystemExit(f"model directory missing: {model}")
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise SystemExit("Install optional CUDA runtime: pip install torch transformers") from exc
    if not torch.cuda.is_available(): raise SystemExit("CUDA is unavailable; refusing CPU fallback")
    tokenizer = AutoTokenizer.from_pretrained(model)
    network = AutoModelForCausalLM.from_pretrained(model, torch_dtype="auto", device_map="cuda")
    messages = [{"role": "system", "content": "Return one JSON object only. operation must be one of: observe, read, locate, type, click, keys, select, screenshot, wait, send."},
                {"role": "user", "content": "Classify: inspect the collaboration inbox."}]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
    output = network.generate(**inputs, max_new_tokens=64, do_sample=False)
    raw = tokenizer.decode(output[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
    match = re.search(r"\{.*?\}", raw, re.S)
    if not match:
        print(json.dumps(safe_fallback(raw, reason="no JSON object in model output"), ensure_ascii=False)); return
    try:
        print(json.dumps({"status": "MODEL_PROPOSAL_VALID", "proposal": validate_proposal(match.group(0)), "cuda": True}, ensure_ascii=False, indent=2))
    except ValueError as exc:
        print(json.dumps(safe_fallback(raw, reason=str(exc)), ensure_ascii=False))

if __name__ == "__main__": main()
