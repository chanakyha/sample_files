"""One-time step (needs internet): prepare all models in ./models.

1. Downloads facebook/nllb-200-distilled-600M (~2.5 GB) to a temporary folder, converts
   it to CTranslate2 with int8 weights (~600 MB) in models/nllb-200-distilled-600M-ct2,
   then deletes the download.
2. Downloads the fastText language-ID model (125 MB, or 1 MB with --compact-lid) into
   models/fasttext-lid.

Requires the conversion packages:  pip install -r requirements-convert.txt

Usage:  python download_models.py [--dir models]
"""

import argparse
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path

# This script is the only part that needs the network; override the engine's offline lock.
os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["TRANSFORMERS_OFFLINE"] = "0"

from ctranslate2.converters import TransformersConverter  # noqa: E402
from huggingface_hub import snapshot_download  # noqa: E402

from translator.detect import FASTTEXT_DIR  # noqa: E402
from translator.engine import MODEL_NAME, local_model_path, model_dir  # noqa: E402

FASTTEXT_URL = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/"

ALLOW = ["*.json", "*.model", "pytorch_model.bin", "model.safetensors"]
TOKENIZER_FILES = [
    "tokenizer.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer_config.json",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=None, help="Target folder (default: ./models)")
    parser.add_argument(
        "--compact-lid", action="store_true",
        help="Use the 1 MB fastText language-ID model instead of the 125 MB one (slightly less accurate)",
    )
    parser.add_argument(
        "--lid-only", action="store_true", help="Only download the language-ID model"
    )
    args = parser.parse_args()

    if args.lid_only:
        download_fasttext(model_dir(args.dir) / FASTTEXT_DIR, compact=args.compact_lid)
        return

    target = local_model_path(args.dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        src = Path(tmp) / "hf"
        print(f"Downloading {MODEL_NAME} (~2.5 GB)...")
        snapshot_download(repo_id=MODEL_NAME, local_dir=src, allow_patterns=ALLOW)

        print(f"Converting to CTranslate2 int8 -> {target}")
        if target.exists():
            shutil.rmtree(target)
        TransformersConverter(str(src), copy_files=TOKENIZER_FILES).convert(
            str(target), quantization="int8"
        )

    size = sum(f.stat().st_size for f in target.iterdir()) / 1e6
    print(f"Translation model ready: {target} ({size:.0f} MB)")

    download_fasttext(model_dir(args.dir) / FASTTEXT_DIR, compact=args.compact_lid)
    print("Done.")


def download_fasttext(folder: Path, compact: bool) -> None:
    """fastText language-ID model (CC BY-SA 3.0), used by the language detector."""
    name = "lid.176.ftz" if compact else "lid.176.bin"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    if dest.is_file():
        print(f"Language-ID model already present: {dest}")
        return
    print(f"Downloading fastText {name} -> {dest}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(FASTTEXT_URL + name, tmp)
    tmp.replace(dest)


if __name__ == "__main__":
    main()
