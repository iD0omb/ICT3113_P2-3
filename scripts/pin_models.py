"""Record the exact tag and digest of every pulled model, plus the Ollama version.

Usage: python scripts/pin_models.py [--ollama http://localhost:11434] [--out models.lock.json]
"""
import argparse
import json
import urllib.request
from datetime import datetime, timezone


def get(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ollama", default="http://localhost:11434")
    parser.add_argument("--out", default="models.lock.json")
    args = parser.parse_args()

    base = args.ollama.rstrip("/")
    tags = get(f"{base}/api/tags")["models"]
    lock = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "ollama_version": get(f"{base}/api/version")["version"],
        "models": sorted(
            (
                {
                    "tag": m["name"],
                    "digest": m["digest"],
                    "size_bytes": m["size"],
                    "parameter_size": m.get("details", {}).get("parameter_size"),
                    "quantization": m.get("details", {}).get("quantization_level"),
                    "family": m.get("details", {}).get("family"),
                }
                for m in tags
            ),
            key=lambda m: m["tag"],
        ),
    }
    with open(args.out, "w") as f:
        json.dump(lock, f, indent=2)
    for m in lock["models"]:
        print(f'{m["tag"]:24} {m["digest"]}  {m["parameter_size"]} {m["quantization"]}')
    print(f'Ollama {lock["ollama_version"]} -> {args.out}')


if __name__ == "__main__":
    main()
