# Model training

The Translator API uses two models that we trained from scratch on public datasets. No
pretrained or Hugging Face models are used.

| Model | Task | Architecture | Size | Output folder |
|-------|------|--------------|------|---------------|
| **NMT** (neural machine translation) | Translate en ↔ es ↔ fr (6 directions) | Transformer encoder–decoder, 6 encoder + 2 decoder layers, d_model 256, 10.9M parameters, own SentencePiece tokenizer (16k pieces) | 22 MB | `TranslatorService/models/nmt-en-es-fr/` |
| **LID** (language identification) | Detect 24 EU languages | fastText-style: hashed words + character n-grams → averaged embedding (dim 32) → linear layer | 65 MB | `TranslatorService/models/lid/` |

All paths below are relative to `TranslatorService/`. To install and run the API, see
[PYTHON_API_SETUP.md](PYTHON_API_SETUP.md).

---

## 1. Environment

### CPU (enough for everything, but NMT training is slow)

```powershell
cd TranslatorService
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-train.txt      # runtime packages + sacrebleu + truststore
```

[requirements-train.txt](../TranslatorService/requirements-train.txt) adds:
* `sacrebleu` (2.6.0), which computes BLEU/chrF scores;
* `truststore`, which checks HTTPS downloads against the Windows certificate store.

### GPU (recommended for NMT training)

A separate environment with a CUDA build of PyTorch keeps the runtime `.venv` small. CUDA 12.6
builds still support older GPUs. The shipped model was trained on a GeForce MX130 (2 GB,
compute capability 5.0) with torch 2.14.1+cu126.

```powershell
python -m venv .venv-gpu
.venv-gpu\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu126
.venv-gpu\Scripts\pip install sentencepiece numpy sacrebleu
.venv-gpu\Scripts\python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Only `train_nmt.py` uses the GPU. All other scripts run on the CPU in `.venv`.

## 2. Pipeline overview

```
 download_data.py ──> data/raw/ ──┬──> prepare_data.py ──> data/prepared/ ──> train_nmt.py ──> models/nmt-en-es-fr/
   (internet, once)               │      (+ domain_data.py,                    (CPU or GPU,        │
                                  │       domain_test.tsv)                      resumable)          v
                                  │                                                         evaluate_nmt.py
                                  └──> train_lid.py ──> models/lid/                         tools/score_api.py
                                         (+ lid_phrases.tsv; evaluates on lid_challenge.tsv,
                                            lid_everyday.tsv, lid_everyday2.tsv)
```

| Step | Command | Time (4-core laptop) | Needs |
|------|---------|----------------------|-------|
| 1 | `python training\download_data.py` | 5–15 min | Internet; about 880 MB |
| 2 | `python training\prepare_data.py` | about 25 min | Steps 1; about 470 MB output |
| 3 | `python training\train_lid.py` | about 45 min | Step 1 |
| 4 | `python training\train_nmt.py …` | hours (see section 6) | Step 2 |
| 5 | `python training\evaluate_nmt.py` | about 4 min | Steps 2 and 4 |

Steps 3 and 4 are independent and can run at the same time (LID on the CPU, NMT on the GPU).

## 3. Datasets (step 1: `download_data.py`)

[training/download_data.py](../TranslatorService/training/download_data.py) downloads into
`training/data/raw/`:

| Dataset | Used for | Files | Licence |
|---------|----------|-------|---------|
| [Tatoeba](https://opus.nlpl.eu/Tatoeba/corpus/version/Tatoeba) (OPUS v2026-07-08) | NMT: short everyday sentences | `parallel/Tatoeba_{en-fr,en-es,es-fr}.zip` | CC BY 2.0 FR |
| [Europarl v8](https://opus.nlpl.eu/Europarl/corpus/version/Europarl) (OPUS) | NMT: parliament proceedings (formal) | `parallel/Europarl_*.zip` | Free |
| [News-Commentary v16](https://opus.nlpl.eu/News-Commentary/corpus/version/News-Commentary) (OPUS) | NMT: news analysis | `parallel/NewsCommentary_*.zip` | Free |
| [Tatoeba sentences](https://tatoeba.org/downloads) | LID: sentences in the 24 languages | `mono/{bul,ces,…,swe}_sentences.tsv.bz2` | CC BY 2.0 FR |
| [QED](https://opus.nlpl.eu/QED/corpus/version/QED) (OPUS v2.0a) | LID: extra data for 10 languages with few Tatoeba sentences (bg, el, et, ga, hr, lv, mt, ro, sk, sl) | `mono/QED_{en-xx}.zip` | CC BY-NC-ND 4.0 |

Files that already exist are skipped, so the script can be rerun after an interruption.

## 4. Data preparation (step 2: `prepare_data.py`)

[training/prepare_data.py](../TranslatorService/training/prepare_data.py)

```powershell
python training\prepare_data.py                    # first time: also trains the tokenizer
python training\prepare_data.py --reuse-tokenizer  # later: keep spm.model so existing checkpoints stay valid
```

Options: `--europarl-per-pair 300000`, `--dev-per-corpus 200`, `--test-per-corpus 500`.

What it does:

1. **Reads and cleans** each OPUS zip. It drops a pair when:
   * a side is empty, or both sides are identical;
   * a side has no letters, or is longer than 400 characters;
   * the length ratio is above 2.2 (likely misaligned);
   * the pair is a duplicate.

   It keeps at most 300,000 Europarl pairs per language pair.
2. **Holds out dev and test sets** (seed 1234, always the same). From Tatoeba and News-Commentary,
   per language pair: 500 test + 200 dev pairs, used in both directions. These sentences never
   appear in training.
3. **Adds the domain test set**: [training/domain_test.tsv](../TranslatorService/training/domain_test.tsv),
   45 hand-written car-service, greeting and name sentences (en / es / fr per line), in all 6
   directions, with corpus `Domain`. They are also excluded from training.
4. **Builds training examples in both directions** (a → b and b → a). Augmentations:
   * **Casual:** for 15% of Tatoeba pairs, an extra copy with the source lowercased and the
     final punctuation removed, so quickly typed input works.
   * **Names** ([training/domain_data.py](../TranslatorService/training/domain_data.py),
     `name_augment`): in Tatoeba pairs that contain "Tom"/"Mary" on both sides, the names are
     replaced on both sides with random real or made-up names (about 46k examples). This teaches
     the model to copy names it has never seen.
   * **Domain** (`domain_triplets`): about 2,200 en/es/fr car-service, greeting and introduction
     sentences, built from hand-written templates and word lists (car parts, times, noises,
     names), in all 6 directions. Any generated sentence that matches a test sentence is skipped.
5. **Trains the tokenizer**: SentencePiece unigram, 16,000 pieces, shared by all three
   languages, `byte_fallback` (no unknown characters). Special tokens: pad 0, unk 1, bos 2,
   eos 3, and the language tags `<2en>`, `<2es>`, `<2fr>`.
6. **Encodes and saves** the examples, keeping those of at most 128 tokens per side.

Output (`training/data/prepared/`):

| File | Contents |
|------|----------|
| `spm.model`, `spm.vocab` | Tokenizer |
| `train_src.bin`, `train_tgt.bin` | Token ids (uint16), all examples concatenated |
| `train_src_off.npy`, `train_tgt_off.npy` | Start offset of each example |
| `train_dir.npy` | Direction of each example (0–5: en→es, en→fr, es→en, es→fr, fr→en, fr→es) |
| `train_corpus.npy` | Corpus of each example (0 Tatoeba, 1 NewsCommentary, 2 Europarl, 3 Domain, 4 Names) |
| `dev.tsv` | 2,400 dev sentences: `src_lang, tgt_lang, source, reference, corpus` |
| `test.tsv` | 6,270 test sentences (6,000 Tatoeba/News + 270 Domain), same columns |

The current data has 4,024,940 training examples (Tatoeba 1.32M, News-Commentary 0.85M,
Europarl 1.80M, Domain 13k, Names 46k) and about 104M target tokens per pass.

## 5. Language detection model (step 3: `train_lid.py`)

[training/train_lid.py](../TranslatorService/training/train_lid.py) · model code
[translator/lid.py](../TranslatorService/translator/lid.py) · runtime
[translator/detect.py](../TranslatorService/translator/detect.py)

```powershell
python training\train_lid.py
```

### Data

* **Sentences:** Tatoeba sentences per language. 500 per language are held out for testing.
  Training uses 40,000 per language, and 120,000 for **en, es, fr** (the languages the
  translator supports, `--main-langs`, `--main-per-lang`). Languages with fewer sentences get QED
  subtitles; small languages are oversampled.
* **Cleaning:** lines in other languages' data that are really untranslated English (common in
  subtitles, e.g. "Yes.", "Hello") are dropped (`english_like`).
* **Augmentation:** an extra copy of half the data. In 25% of it a foreign name is inserted
  (so names do not count as evidence of a language). In 35% the punctuation is removed. In 40%
  it is cut to the first 1–4 words (very short input).
* **Short chat phrases:** [training/lid_phrases.tsv](../TranslatorService/training/lid_phrases.tsv)
  has hand-written greetings and short phrases in all 24 languages ("hi there", "salut
  tout le monde", "hola a todos", "tschüss", …). Each is added 40 times
  (`--phrase-repeat`) with casing, punctuation and name variants. Any phrase that also appears
  in an evaluation set is dropped automatically, so evaluation stays blind.

### Model

`features()` turns a text into hashed ids:
* each lowercased word, counted twice;
* the character 2- to 4-grams of each word, with `<` and `>` marking the word boundaries;
* everything hashed with CRC32 into 2²⁰ buckets.

`LIDModel` averages the embeddings of these ids (EmbeddingBag, dimension 32), then applies a
linear layer with 24 outputs.

### Training

| Setting | Value |
|---------|-------|
| Optimiser | SparseAdam (embeddings) + Adam (output layer), learning rate 0.01 |
| Batch | 256 texts |
| Epochs | 5 (about 7 min each) |
| Examples | 1.80M training, 12,000 held-out test |

### Prior for en / es / fr

After training, `calibrate_prior` chooses the largest log-prior boost for en/es/fr that costs
the other 21 languages at most 0.5% accuracy (`--max-prior-cost 0.005`). It is measured on
held-out sentences and 1–3-word fragments. The chosen boost is **1.5**. It only decides
ambiguous short input. It is stored in `models/lid/config.json` (`"prior"`) and added to the
logits in `detect.py`.

### Evaluation sets (never used for training)

| File | Contents |
|------|----------|
| [training/lid_challenge.tsv](../TranslatorService/training/lid_challenge.tsv) | 143 phrases in many languages: short phrases, sentences with foreign names, casual text |
| [training/lid_everyday.tsv](../TranslatorService/training/lid_everyday.tsv) | 150 everyday en/fr/es phrases, greetings, car-service sentences |
| [training/lid_everyday2.tsv](../TranslatorService/training/lid_everyday2.tsv) | 70 more casual phrases (blind set) |

### Results (current model)

| Test | Accuracy |
|------|----------|
| Held-out sentences, 24 languages | 98.7% |
| Challenge set | 142 / 143 |
| Everyday en/fr/es | 147 / 150 |
| Everyday 2 | 69 / 70 |
| Source language of the 6,270 translation test sentences (through the API) | 99.8% |

The remaining misses are single words that exist in several languages ("hi" is Maltese for
"she", "no" is also Spanish, "non" is also Italian).

Output: `models/lid/lid.pt` (fp16 weights) and `models/lid/config.json` (languages, buckets,
dim, n-gram settings, prior, accuracies). Log: `training/logs/train_lid_v2.log`.

## 6. Translation model (step 4: `train_nmt.py`)

[training/train_nmt.py](../TranslatorService/training/train_nmt.py) · model code
[translator/nmt.py](../TranslatorService/translator/nmt.py) · runtime
[translator/engine.py](../TranslatorService/translator/engine.py)

### Model ([translator/nmt.py](../TranslatorService/translator/nmt.py))

* Transformer encoder–decoder (Vaswani et al., 2017), pre-layer-norm, sinusoidal positions.
* 6 encoder layers, 2 decoder layers, d_model 256, 4 attention heads, feed-forward 1024,
  dropout 0.1: 10.9M parameters.
* One shared embedding matrix for the encoder, the decoder and the output layer.
* The target language is chosen by a tag token at the start of the source (`<2fr> Hello .`),
  so one model serves all 6 directions.
* Decoding: greedy or beam search (beam 4 by default), with key/value caching. The language
  tags are banned from the output.

### Training settings

| Setting | Default | Notes |
|---------|---------|-------|
| `--hours` | 8 | Stops after this time (plus a final evaluation) |
| `--max-tokens` | 6000 | Padded tokens per micro-batch; batches group sentences of similar length |
| `--accum` | 1 | Micro-batches per optimiser step (effective batch = max-tokens × accum) |
| `--device` | auto | GPU if available |
| `--mix` | all once | Corpus weights per epoch, e.g. `Tatoeba=3,Europarl=0.3`: 3 = repeat 3×, 0.3 = random 30% |
| `--lr` / `--warmup` | 1e-3 / 400 | Inverse-square-root schedule |
| `--label-smoothing` | 0.1 | |
| Optimiser | AdamW | betas (0.9, 0.98), eps 1e-9, weight decay 1e-4, gradient clipping 1.0 |
| `--eval-minutes` | 30 | Dev BLEU (greedy, 60 sentences per direction) |
| `--d-model`, `--ff`, `--heads`, `--enc-layers`, `--dec-layers` | 256, 1024, 4, 6, 2 | Architecture |
| `--resume` | | Continue from `training/checkpoints/last.pt` |
| `--new-epoch` | | With `--resume`: start a fresh epoch (after changing `--mix` or the data) |
| `--reset-best` | | With `--resume`: forget the best dev BLEU (needed when fine-tuning on new data) |
| `--benchmark N` | | Run N steps, print the speed, exit |

How a run behaves:
* **Every 30 minutes** it computes dev BLEU and saves `training/checkpoints/last.pt` (weights,
  optimiser and progress, so it can resume exactly). When dev BLEU improves, it **exports** the
  model to `models/nmt-en-es-fr/`: `model.pt` (fp16), `spm.model`, and `config.json`
  (architecture, languages, step, dev BLEU). The API can use the exported model while training
  continues.
* **Stopping early:** create the file `training\STOP` (or press Ctrl+C). It saves and exits.
* **Progress** is in `training/logs/train_nmt.log`.

Speed: about 1,100 target tokens/s on a 4-core laptop CPU, about 3,000 tokens/s on a GeForce
MX130. On a 2 GB GPU, use `--max-tokens 3000 --accum 2`; 6000 does not fit in memory and is
slower.

### How the shipped model was trained (exact history)

| Phase | Hardware | Command | Steps | Time | Dev BLEU |
|-------|----------|---------|-------|------|----------|
| 1. Base training | CPU, i5-1035G1, 4 threads | `python training\train_nmt.py --hours <n>` (default settings, all data once, no mix), stopped with `training\STOP` to move to the GPU | 0 → 3,785 | 5.8 h | 6.20 |
| 2. More everyday data | GeForce MX130 | `.venv-gpu\Scripts\python training\train_nmt.py --resume --new-epoch --hours 10 --max-tokens 3000 --accum 2 --mix "Tatoeba=3,NewsCommentary=1,Europarl=0.3"` | → 20,031 (best 18,538) | 10 h | 24.26 |
| 3. Domain fine-tuning | GeForce MX130 | `python training\prepare_data.py --reuse-tokenizer`, then `.venv-gpu\Scripts\python training\train_nmt.py --resume --new-epoch --reset-best --hours 3 --max-tokens 3000 --accum 2 --mix "Tatoeba=1,NewsCommentary=0.5,Europarl=0.15,Domain=8,Names=1"` | → 25,489 (**best 24,486**, exported) | 3 h | **25.92** |

Total: about 19 hours. Phase 3 needs the prepared data to contain the Domain and Names corpora;
step 2 now always adds them.

### Training from scratch (recommended recipe)

```powershell
python training\download_data.py
python training\prepare_data.py
python training\train_lid.py
.venv-gpu\Scripts\python training\train_nmt.py --hours 15 --max-tokens 3000 --accum 2 `
    --mix "Tatoeba=3,NewsCommentary=1,Europarl=0.3,Domain=4,Names=1"
python training\evaluate_nmt.py
```

More training hours give better translations, so continue a run with `--resume`. To improve a
finished model on new domain sentences:
1. add templates or phrases to `training/domain_data.py`;
2. rerun `prepare_data.py --reuse-tokenizer`;
3. fine-tune as in phase 3.

## 7. Evaluation (step 5)

```powershell
python training\evaluate_nmt.py                    # exported model, beam 4, whole test set
python training\evaluate_nmt.py --corpus Domain    # only the domain set
python tools\score_api.py                          # through the running API, with auto-detection
```

[training/evaluate_nmt.py](../TranslatorService/training/evaluate_nmt.py) prints BLEU and chrF
(sacrebleu) per direction and per corpus. [tools/score_api.py](../TranslatorService/tools/score_api.py)
sends the same test set through the API (for example the load balancer) without
`source_lang`, so language detection is part of the score. It also reports detection accuracy.

### Results of the shipped model (6,270 held-out sentences, beam 4)

| Direction | Everyday (Tatoeba) BLEU / chrF | News BLEU / chrF | Domain BLEU / chrF |
|-----------|-------------------------------|------------------|--------------------|
| en → es | 35.4 / 57.6 | 26.7 / 54.0 | 61.8 / 77.9 |
| en → fr | 35.6 / 56.6 | 19.5 / 48.4 | 58.4 / 76.7 |
| es → en | 42.0 / 60.2 | 24.6 / 53.7 | 60.6 / 74.1 |
| es → fr | 38.5 / 58.4 | 21.0 / 50.6 | 57.8 / 74.2 |
| fr → en | 41.3 / 58.3 | 20.6 / 48.8 | 63.6 / 77.2 |
| fr → es | 35.2 / 57.3 | 22.5 / 51.5 | 54.7 / 74.7 |
| **all** | **38.1 / 58.0** | **22.7 / 51.2** | **59.8 / 75.8** |

**Overall: BLEU 26.3, chrF 52.8.** Through the API with automatic detection: BLEU 26.3, chrF
52.8, source language correct for 99.8%. History of the score:

| Model | Overall BLEU | Domain BLEU |
|-------|--------------|-------------|
| Phase 1 (CPU) | not measured (dev BLEU 6.20) | not measured |
| Phase 2 (step 18,538) | 24.5 | 36.8 |
| Phase 3, shipped (step 24,486) | **26.3** | **59.8** |

Quality: short everyday and car-service sentences are mostly correct, and unusual names are
usually copied correctly. Long news sentences are understandable but contain errors. Results
are saved in `training/logs/evaluate_final.txt` and `training/logs/score_api_final.txt`.

## 8. Reference files

### Training scripts and data definitions (`TranslatorService/training/`)

| File | Role |
|------|------|
| [download_data.py](../TranslatorService/training/download_data.py) | Step 1: downloads OPUS parallel corpora and Tatoeba / QED monolingual data |
| [prepare_data.py](../TranslatorService/training/prepare_data.py) | Step 2: cleaning, dev/test split, augmentation, tokenizer, encoding |
| [domain_data.py](../TranslatorService/training/domain_data.py) | Hand-written domain templates (car parts, times, noises, introductions, phrases) and the name augmentation |
| [domain_test.tsv](../TranslatorService/training/domain_test.tsv) | 45 held-out domain test sentences (en / es / fr) |
| [train_lid.py](../TranslatorService/training/train_lid.py) | Step 3: trains and evaluates the language detector, calibrates the prior, exports `models/lid/` |
| [lid_phrases.tsv](../TranslatorService/training/lid_phrases.tsv) | Training: greetings / short phrases in 24 languages |
| [lid_challenge.tsv](../TranslatorService/training/lid_challenge.tsv) | Evaluation: LID challenge set (143) |
| [lid_everyday.tsv](../TranslatorService/training/lid_everyday.tsv) | Evaluation: everyday en/fr/es (150) |
| [lid_everyday2.tsv](../TranslatorService/training/lid_everyday2.tsv) | Evaluation: casual phrases (70) |
| [train_nmt.py](../TranslatorService/training/train_nmt.py) | Step 4: trains the translation model on CPU/GPU, resumable, exports `models/nmt-en-es-fr/` |
| [evaluate_nmt.py](../TranslatorService/training/evaluate_nmt.py) | Step 5: BLEU / chrF on the test set |
| [../tools/score_api.py](../TranslatorService/tools/score_api.py) | Score through the running API |

### Model code (`TranslatorService/translator/`), shared by training and the API

| File | Role |
|------|------|
| [nmt.py](../TranslatorService/translator/nmt.py) | Transformer model, greedy and beam search |
| [engine.py](../TranslatorService/translator/engine.py) | Runtime translator: loads the model, splits sentences, batches by length, removes duplicates, LRU cache |
| [lid.py](../TranslatorService/translator/lid.py) | Language detector model and feature hashing |
| [detect.py](../TranslatorService/translator/detect.py) | Runtime detector: loads `models/lid/`, applies the prior |
| [languages.py](../TranslatorService/translator/languages.py) | The 24 EU language codes and names |

### Generated files (not in the repository except `models/`)

| Path | Created by | Size | In git |
|------|-----------|------|--------|
| `training/data/raw/` | download_data.py | ~880 MB | no |
| `training/data/prepared/` | prepare_data.py | ~470 MB | no |
| `training/checkpoints/last.pt` | train_nmt.py (resumable state) | 126 MB | no |
| `models/nmt-en-es-fr/` | train_nmt.py (export) | 22 MB | **yes** |
| `models/lid/` | train_lid.py | 65 MB | **yes** |
| `training/logs/*.log`, `*.txt` | all scripts | small | yes (training history and scores) |

### Logs and results (`TranslatorService/training/logs/`)

| File | Contents |
|------|----------|
| `train_nmt.log` | Every 100 steps: loss, learning rate, speed. Every eval: dev BLEU per direction and sample translations. All 3 phases. |
| `prepare.log`, `prepare_v2.log` | Data counts (v2 includes the Domain/Names corpora) |
| `train_lid.log`, `train_lid_v2.log` | LID training (v2 = current model): accuracy per epoch and language, prior calibration, evaluation misses |
| `evaluate_final.txt` | Final test scores of the shipped model |
| `evaluate_v2.txt` | The same scores, right after fine-tuning |
| `evaluate_domain_before.txt` | Domain scores before fine-tuning (36.8 BLEU) |
| `score_api_final.txt` | Scores through the load-balanced API, with detection accuracy |

## 9. Troubleshooting

| Problem | Fix |
|---------|-----|
| `CUDA out of memory` | Lower `--max-tokens` (e.g. 2000) and raise `--accum` |
| Training very slow on GPU | Larger `--max-tokens` may overflow the GPU memory into shared RAM; use `--benchmark 50` to compare settings |
| Existing checkpoint gives garbage after `prepare_data.py` | The tokenizer changed. Always use `--reuse-tokenizer` when continuing an existing model. |
| Fine-tuned model is never exported | Use `--reset-best` when the dev BLEU of the new run may be lower than the old best |
| SSL errors in `download_data.py` | `pip install truststore` (uses the Windows certificate store) |
| API still serves the old model | Restart the API (models are loaded at startup) |
