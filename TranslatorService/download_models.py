"""One-time step (needs internet): download the translation model into ./models.

The model is stored as float16 safetensors (~1.2 GB instead of ~2.5 GB) to make it
easier to copy between machines. After this, copy the project folder (including
./models) to any machine and the API runs with no internet connection. The language
detectors (langid, lingua) ship their models inside their pip packages.

Usage:
  python download_models.py              # download + shrink to fp16
  python download_models.py --no-shrink  # keep the original float32 weights
  python download_models.py --shrink-only  # convert an already-downloaded model
"""

import argparse
import os
import shutil
from pathlib import Path

# This script is the only part that needs the network; override the engine's offline lock.
os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["TRANSFORMERS_OFFLINE"] = "0"

import torch  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402
from transformers import AutoModelForSeq2SeqLM  # noqa: E402

from translator.engine import MODEL_NAME, local_model_path  # noqa: E402

ALLOW = ["*.json", "*.model", "pytorch_model.bin", "model.safetensors"]


def shrink(path: Path) -> None:
    """Re-save the weights as float16 safetensors and drop the float32 file."""
    if (path / "model.safetensors").is_file() and not (path / "pytorch_model.bin").is_file():
        print("Already shrunk.")
        return
    print("Converting weights to float16 safetensors...")
    model = AutoModelForSeq2SeqLM.from_pretrained(path, dtype=torch.float16)
    model.save_pretrained(path, safe_serialization=True)
    (path / "pytorch_model.bin").unlink(missing_ok=True)
    size = (path / "model.safetensors").stat().st_size / 1e9
    print(f"Saved model.safetensors ({size:.2f} GB)")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=None, help="Target folder (default: ./models)")
    parser.add_argument("--no-shrink", action="store_true", help="Keep float32 weights")
    parser.add_argument("--shrink-only", action="store_true", help="Only convert, no download")
    args = parser.parse_args()

    target = local_model_path(MODEL_NAME, args.dir)
    if not args.shrink_only:
        print(f"Downloading {MODEL_NAME} -> {target} (~2.5 GB)")
        snapshot_download(repo_id=MODEL_NAME, local_dir=target, allow_patterns=ALLOW)
        shutil.rmtree(target / ".cache", ignore_errors=True)
    if not args.no_shrink:
        shrink(target)
    print("Done.")


if __name__ == "__main__":
    main()
