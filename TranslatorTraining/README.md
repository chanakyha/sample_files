# Model training

Scripts, data and logs to train the two models of the translator from scratch. The trained models
are written into [../TranslatorServer/models/](../TranslatorServer/models/), which the API server
loads; the model code itself is shared with the server
([../TranslatorServer/translator/](../TranslatorServer/translator/)), so both always match.

This folder is only needed to retrain. It has its own Python environment (`.venv`, and
`.venv-gpu` for an NVIDIA GPU), separate from the server's. Full documentation:
[../docs/MODEL_TRAINING.md](../docs/MODEL_TRAINING.md).

| File | Step |
|------|------|
| [download_data.py](download_data.py) | 1. Download the training data from GitHub (`--proxy URL`) into `data/raw/` |
| [prepare_data.py](prepare_data.py) | 2. Clean, split dev/test, augment, train the tokenizer, encode into `data/prepared/` |
| [train_lid.py](train_lid.py) | 3. Train the language detector, export `../TranslatorServer/models/lid/` |
| [train_nmt.py](train_nmt.py) | 4. Train the translation model, export `../TranslatorServer/models/nmt-en-es-fr/` |
| [evaluate_nmt.py](evaluate_nmt.py) | 5. BLEU / chrF on the held-out test set |
| [score_api.py](score_api.py) | Score through the running API |
| [domain_data.py](domain_data.py), `*.tsv` | Domain sentences, names, detector phrases and evaluation sets |
| `logs/` | Logs and scores of every run (`opus_v1/`: the previous models) |

## Training data (downloaded from GitHub only)

The original dataset hosts and Kaggle are blocked on the corporate network, so
[download_data.py](download_data.py) takes everything from public GitHub
repositories (github.com, codeload.github.com, raw.githubusercontent.com):

| Data | GitHub repository | Used for | Licence |
|------|-------------------|----------|---------|
| Tatoeba (official OPUS copy) | [Helsinki-NLP/Tatoeba-Challenge](https://github.com/Helsinki-NLP/Tatoeba-Challenge) | translation: everyday sentences; detection | CC BY 2.0 FR |
| Tatoeba en-fr / en-es | [viljarpuusepp/Attention](https://github.com/viljarpuusepp/Attention) (`fra.txt`), [doozan/spanish_data](https://github.com/doozan/spanish_data) (`sentences.tsv`) | translation: everyday sentences | CC BY 2.0 FR |
| Europarl (XML per session) | [UDS-SFB-B6-Datasets/Multilingual-Parallel-Direct-Europarl](https://github.com/UDS-SFB-B6-Datasets/Multilingual-Parallel-Direct-Europarl) | translation: formal text (300k pairs per language pair); detection (de, it, nl, pt) | no copyright restrictions |
| Universal Dependencies treebanks | [UniversalDependencies](https://github.com/UniversalDependencies) (28 repositories) | detection: sentences in all 24 languages | mostly CC BY-SA 4.0 (per treebank) |

The address of every downloaded file: [../docs/MODEL_TRAINING.md](../docs/MODEL_TRAINING.md#download-addresses-of-every-file).
How detection and translation work, in plain words: [../docs/HOW_IT_WORKS.md](../docs/HOW_IT_WORKS.md).

## Training (needs GitHub access only for step 1)

```powershell
python -m venv .venv                  # or: setup.cmd --training (repository root)
.venv\Scripts\activate
pip install -r requirements.txt

python download_data.py      # ~1.1 GB from GitHub into data/raw (--proxy URL)
python prepare_data.py       # clean, split dev/test, train tokenizer, encode (~20 min)
python train_lid.py          # language detector (~45 min)
python train_nmt.py --hours 10
```

`train_nmt.py` scores the dev set every 30 minutes and exports the best model so far to
`../TranslatorServer/models/nmt-en-es-fr/`, so the API can be used while training continues. To stop early, create
the file `STOP` (it saves and exits); continue later with `--resume`. Progress is in
`logs\train_nmt.log`.

Training on a processor (CPU) works but is slow; a graphics card (GPU) is much faster. The
shipped model was trained on a GPU.

### Training on a GPU

Training (only training; the API runs on the CPU) can use an NVIDIA GPU. Install a CUDA build of
PyTorch into a separate environment (`setup.cmd --training --gpu` creates it) so `.venv` stays small. CUDA 12.6 builds still
support older NVIDIA GPUs; 2 GB of GPU memory is enough. The shipped model:

```powershell
python -m venv .venv-gpu
.venv-gpu\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu126
.venv-gpu\Scripts\pip install sentencepiece numpy sacrebleu requests httpx
# phase 1: 14 h
.venv-gpu\Scripts\python train_nmt.py --hours 14 --max-tokens 3000 --accum 2 `
    --mix "Tatoeba=3,Europarl=0.3,Domain=4,Names=1"
# phase 2: 3 h fine-tuning on the domain sentences
.venv-gpu\Scripts\python train_nmt.py --resume --new-epoch --reset-best --hours 3 `
    --max-tokens 3000 --accum 2 --mix "Tatoeba=1,Europarl=0.15,Domain=8,Names=1"
```

`--device auto` (default) picks the GPU when available. `--max-tokens` × `--accum` is the batch
size per step; lower `--max-tokens` if the GPU runs out of memory. `--mix` weights the corpora:
the short everyday Tatoeba sentences are repeated, the long parliament text is subsampled.

### Results

Language detection (held-out sentences, up to 500 per language): **98.8%** (Slovenian 92%, all
others 98–100%). Challenge set of 143 short phrases, sentences with foreign names and casual
text in many languages ([lid_challenge.tsv](lid_challenge.tsv)): **139/143**.
Everyday English/French/Spanish phrases, greetings and car-service sentences
([lid_everyday.tsv](lid_everyday.tsv)): **146/150**; a second set of casual
phrases ([lid_everyday2.tsv](lid_everyday2.tsv)): **70/70**. The remaining
misses are single words that exist in several languages ("no", "non"; "hi" is Maltese for
"she") and short phrases of languages with little GitHub data ("Ďakujem pekne", "Köszönöm
szépen"). These sets are used only for evaluation: `train_lid.py` drops any line of
[lid_phrases.tsv](lid_phrases.tsv) (hand-written greetings and short chat
phrases in all 24 languages, used for training) that also occurs in an evaluation set.

The detector gives English, Spanish and French (the languages the translator supports) a small
log-prior of 1.5, chosen on held-out data so it costs the other 21 languages under 0.5%
accuracy; it only decides genuinely ambiguous short input.

Translation, held-out test set (500 sentences per direction and corpus, never seen in training;
beam 4). "Domain" is [domain_test.tsv](domain_test.tsv): 45 hand-written
car-service, greeting and introduction sentences (with unusual names), excluded from training.
Model from step 26,881 (14 h + 3 h on a GPU), dev BLEU 31.59:

| Direction | Everyday (Tatoeba) BLEU / chrF | Formal (Europarl) BLEU / chrF | Domain BLEU / chrF |
|-----------|------------------|------------------|------------------|
| en → es | 39.9 / 61.2 | 31.5 / 58.0 | 66.7 / 83.9 |
| en → fr | 42.6 / 61.1 | 28.1 / 56.7 | 71.7 / 85.9 |
| es → en | 44.8 / 62.8 | 31.4 / 57.8 | 67.8 / 81.2 |
| es → fr | 38.8 / 59.7 | 28.6 / 56.4 | 64.4 / 80.2 |
| fr → en | 47.9 / 63.6 | 30.0 / 56.8 | 71.4 / 82.6 |
| fr → es | 36.3 / 59.1 | 29.9 / 56.8 | 60.3 / 78.9 |
| **all** | **41.7 / 61.2** | **29.9 / 57.1** | **67.7 / 82.1** |

**Overall BLEU 32.7, chrF 58.2.** The previous model (trained on the OPUS downloads) scores
30.8 / 56.8 on the same test set (Domain 59.8).

Two generated corpora teach the model the car-service domain and names
([domain_data.py](domain_data.py), used by `prepare_data.py`):

* **Domain**: car-service, greeting and introduction sentences in en/es/fr, built from
  hand-written templates and word lists (parts, times, noises, names).
* **Names**: Tatoeba pairs with "Tom"/"Mary" replaced on both sides by random real or made-up
  names, so the model learns to copy names it has never seen ("hi my name is Daniela" →
  "Hola, me llamo Daniela.").

Short everyday and car-service sentences are mostly correct; long parliament text is
understandable but has errors. `GET /health` shows the dev BLEU of the currently exported model;
rerun `python evaluate_nmt.py` after more training, and `python score_api.py` to score
through the running API (with automatic language detection).
