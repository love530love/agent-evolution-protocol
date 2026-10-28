"""Minimal CUDA smoke runner; model output is advisory and never executes tools."""
import argparse
from pathlib import Path

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
    prompt = "Return JSON with intent, operation, confidence, reason. Classify: inspect the collaboration inbox."
    inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
    output = network.generate(**inputs, max_new_tokens=96)
    print(tokenizer.decode(output[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True))

if __name__ == "__main__": main()
