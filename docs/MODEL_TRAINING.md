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
pip install -r requirements-train.txt      # runtime packages + sacrebleu + requests
```

[requirements-train.txt](../TranslatorService/requirements-train.txt) adds:
* `sacrebleu` (2.6.0), which computes BLEU/chrF scores;
* `requests`, which `download_data.py` uses for the downloads: through the `--proxy` (for
  http and https), with `verify=False`, so corporate HTTPS inspection does not cause
  certificate errors, and `timeout=30` seconds.

`pip` itself needs access to pypi.org. If pypi.org is blocked, install the packages from wheel
files copied from another machine (see the offline section of
[PYTHON_API_SETUP.md](PYTHON_API_SETUP.md)).

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
   (GitHub only, once)            │      (+ domain_data.py,                    (CPU or GPU,        │
                                  │       domain_test.tsv)                      resumable)          v
                                  │                                                         evaluate_nmt.py
                                  └──> train_lid.py ──> models/lid/                         tools/score_api.py
                                         (+ lid_phrases.tsv; evaluates on lid_challenge.tsv,
                                            lid_everyday.tsv, lid_everyday2.tsv)
```

| Step | Command | Time (4-core laptop) | Needs |
|------|---------|----------------------|-------|
| 1 | `python training\download_data.py` | 15–30 min | Access to GitHub only; about 1.1 GB download |
| 2 | `python training\prepare_data.py` | about 20 min | Step 1; about 350 MB output |
| 3 | `python training\train_lid.py` | about 45 min | Step 1 |
| 4 | `python training\train_nmt.py …` | hours (see section 6) | Step 2 |
| 5 | `python training\evaluate_nmt.py` | about 4 min | Steps 2 and 4 |

Steps 3 and 4 are independent and can run at the same time (LID on the CPU, NMT on the GPU).

## 3. Datasets (step 1: `download_data.py`)

[training/download_data.py](../TranslatorService/training/download_data.py) downloads **only
from GitHub** (github.com, codeload.github.com, raw.githubusercontent.com), because the original
dataset hosts (opus.nlpl.eu, object.pouta.csc.fi, tatoeba.org) and Kaggle are blocked on the
corporate network. It contacts no other server.

```powershell
python training\download_data.py           # download (cached) + convert
python training\download_data.py --list    # print every URL and its file name in the cache
python training\download_data.py --proxy http://proxy.corp:8080            # through a proxy
python training\download_data.py --proxy http://user:password@proxy.corp:8080
```

From the repository root, [download_data.cmd](../download_data.cmd) runs the same script with
the right Python environment and passes the options on:

```powershell
.\download_data.cmd
.\download_data.cmd --proxy http://proxy.corp:8080
```

#### Sources

| Data | GitHub repository | Files used | Used for | Licence |
|------|-------------------|------------|----------|---------|
| Tatoeba (official OPUS copy) | [Helsinki-NLP/Tatoeba-Challenge](https://github.com/Helsinki-NLP/Tatoeba-Challenge) | `data/dev/{eng-fra,eng-spa,fra-spa}/dev.txt`, `data/devtest/…/test-v2023-04-12.txt`, and the `eng-xx` files of the other 21 languages | NMT everyday pairs; LID sentences | CC BY 2.0 FR |
| Tatoeba en-fr (manythings.org export) | [viljarpuusepp/Attention](https://github.com/viljarpuusepp/Attention) | `data/fra.txt` | NMT everyday pairs | CC BY 2.0 FR |
| Tatoeba en-es | [doozan/spanish_data](https://github.com/doozan/spanish_data) | `sentences.tsv` | NMT everyday pairs | CC BY 2.0 FR |
| Europarl (XML per session and language) | [UDS-SFB-B6-Datasets/Multilingual-Parallel-Direct-Europarl](https://github.com/UDS-SFB-B6-Datasets/Multilingual-Parallel-Direct-Europarl) | `proceedings/xml/{en,es,fr,de,it,nl,pt}/*.xml` (one zip of the repository) | NMT formal pairs; LID sentences for de, it, nl, pt | Europarl: no copyright restrictions |
| Universal Dependencies treebanks | [UniversalDependencies](https://github.com/UniversalDependencies) (28 repositories, e.g. `UD_Irish-IDT`, `UD_Maltese-MUDT`) | the `# text =` lines of all `*.conllu` files | LID sentences in all 24 languages | per treebank, mostly CC BY-SA 4.0; a few are CC BY-NC-SA (e.g. Maltese MUDT); see each repository's `LICENSE.txt` |

Downloads are cached in `training/data/raw/github/`; files that are already there are not
downloaded again, so the script can be rerun after an interruption. If only the github.com
website works (not the scripted download), download the URLs from `--list` in a browser into
that folder and run the script again.

#### What the conversion does

* **Tatoeba:** merges the three copies, removes duplicates, and adds es-fr pairs through a
  shared English sentence (only when that English sentence has exactly one Spanish and one
  French translation).
* **Europarl:** matches each speech by its id in the English, Spanish and French files, then its
  paragraphs, then its sentences (kept only when both paragraphs split into the same number of
  sentences; short paragraphs are kept whole). Paragraphs whose function words point to another
  language are dropped: some speeches were left untranslated in the XML (e.g. Italian text in
  the French file).
* **LID:** for each language, the UD sentences, the Tatoeba sentences, and (de/it/nl/pt) 100,000
  Europarl sentences. For en/es/fr, the Tatoeba side of the translation data.
* Lines with broken encoding are dropped.

#### Output (`training/data/raw/`)

| File | Contents (current data) |
|------|-------------------------|
| `parallel/Tatoeba_{en-fr,en-es,es-fr}.tsv` | 341,867 / 242,469 / 87,620 pairs (`a TAB b`) |
| `parallel/Europarl_{en-fr,en-es,es-fr}.tsv` | 1,397,358 / 1,510,857 / 1,374,545 pairs |
| `mono/<lang>.txt` | `source TAB sentence`; from 2,068 (mt) and 7,523 (ga) to 562,145 (it) sentences per language |

The log of the last run is `training/logs/download_github.log`.

Not available on GitHub, so no longer used: News-Commentary (the old general-text corpus) and
OPUS QED subtitles (the old extra detector data). The previous models were trained on the OPUS
downloads; their logs are kept in `training/logs/opus_v1/`.

## 4. Data preparation (step 2: `prepare_data.py`)

[training/prepare_data.py](../TranslatorService/training/prepare_data.py)

```powershell
python training\prepare_data.py                    # first time: also trains the tokenizer
python training\prepare_data.py --reuse-tokenizer  # later: keep spm.model so existing checkpoints stay valid
```

Options: `--europarl-per-pair 300000`, `--dev-per-corpus 200`, `--test-per-corpus 500`.

What it does:

1. **Reads and cleans** each `parallel/*.tsv` file. It drops a pair when:
   * a side is empty, or both sides are identical;
   * a side has no letters, or is longer than 400 characters;
   * the length ratio is above 2.2 (likely misaligned);
   * the pair is a duplicate.

   It keeps at most 300,000 Europarl pairs per language pair.
2. **Holds out dev and test sets** (seed 1234, always the same). From Tatoeba and Europarl,
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
| `train_corpus.npy` | Corpus of each example (0 Tatoeba, 1 Europarl, 2 Domain, 3 Names) |
| `dev.tsv` | 2,400 dev sentences: `src_lang, tgt_lang, source, reference, corpus` |
| `test.tsv` | 6,270 test sentences (3,000 Tatoeba + 3,000 Europarl + 270 Domain), same columns |

The current data has 3,393,320 training examples (Tatoeba 1.53M, Europarl 1.79M, Domain 13k,
Names 60k) and about 76M target tokens per pass. Log: `training/logs/prepare.log`.

## 5. Language detection model (step 3: `train_lid.py`)

[training/train_lid.py](../TranslatorService/training/train_lid.py) · model code
[translator/lid.py](../TranslatorService/translator/lid.py) · runtime
[translator/detect.py](../TranslatorService/translator/detect.py)

```powershell
python training\train_lid.py
```

### Data

* **Sentences:** `data/raw/mono/<lang>.txt` (Tatoeba + Universal Dependencies, plus Europarl for
  de/it/nl/pt). 500 per language are held out for testing (at most 20% for the smallest
  languages: Maltese has 2,068 sentences, Irish 7,523). Training uses 40,000 per language, and
  120,000 for **en, es, fr** (the languages the translator supports, `--main-langs`,
  `--main-per-lang`). Languages with fewer sentences are oversampled.
* **Cleaning:** lines in other languages' data that are really untranslated English (titles,
  quotes) are dropped (`english_like`).
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
| Epochs | 5 (about 7 min each on an idle CPU; 20–30 min while the GPU training runs) |
| Examples | 1.67M training, 11,901 held-out test |

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
| Held-out sentences, 24 languages | 98.8% (Slovenian 92%, all others 98–100%) |
| Challenge set | 139 / 143 |
| Everyday en/fr/es | 146 / 150 |
| Everyday 2 | 70 / 70 |
| Source language of the 6,270 translation test sentences (through the API) | 99.4% |

The previous model, trained on the OPUS/Tatoeba downloads, scored 98.7%, 142/143, 147/150 and
69/70. The remaining misses:
* single words that exist in several languages ("hi" is Maltese for "she", "no" is also
  Spanish, "non" is also Italian, "no problem" looks Scandinavian);
* short polite phrases of languages with little GitHub data: "Ďakujem pekne" (Slovak → Slovenian),
  "Köszönöm szépen" (Hungarian → German), "do jutra" (Polish → Slovenian).
Slovenian is the weakest language (92% on held-out sentences, mostly confused with Croatian),
because GitHub has little Slovenian text besides the UD treebanks.

Output: `models/lid/lid.pt` (fp16 weights) and `models/lid/config.json` (languages, buckets,
dim, n-gram settings, prior, accuracies). Log: `training/logs/train_lid.log`.

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

Trained from scratch on the GitHub data, on a GeForce MX130 (the language detector trained on
the CPU at the same time):

| Phase | Command (in `TranslatorService/training/`) | Steps | Time | Dev BLEU |
|-------|---------|-------|------|----------|
| 1. Base training | `..\.venv-gpu\Scripts\python train_nmt.py --hours 14 --max-tokens 3000 --accum 2 --mix "Tatoeba=3,Europarl=0.3,Domain=4,Names=1"` | 0 → 22,084 | 14 h | 29.98 |
| 2. Domain fine-tuning | `..\.venv-gpu\Scripts\python train_nmt.py --resume --new-epoch --reset-best --hours 3 --max-tokens 3000 --accum 2 --mix "Tatoeba=1,Europarl=0.15,Domain=8,Names=1"` | → 26,881 (**best 26,881**, exported) | 3 h | **31.59** |

Total: 17 hours. The whole log is `training/logs/train_nmt.log`. (The history of the previous,
OPUS-trained model is in `training/logs/opus_v1/train_nmt.log`.)

### Training from scratch (recommended recipe)

```powershell
cd TranslatorService
.venv\Scripts\python training\download_data.py
.venv\Scripts\python training\prepare_data.py
.venv\Scripts\python training\train_lid.py
.venv-gpu\Scripts\python training\train_nmt.py --hours 14 --max-tokens 3000 --accum 2 `
    --mix "Tatoeba=3,Europarl=0.3,Domain=4,Names=1"
.venv-gpu\Scripts\python training\train_nmt.py --resume --new-epoch --reset-best --hours 3 `
    --max-tokens 3000 --accum 2 --mix "Tatoeba=1,Europarl=0.15,Domain=8,Names=1"
.venv\Scripts\python training\evaluate_nmt.py
```

More training hours give better translations, so continue a run with `--resume`. To improve a
finished model on new domain sentences:
1. add templates or phrases to `training/domain_data.py`;
2. rerun `prepare_data.py --reuse-tokenizer`;
3. fine-tune as in phase 2.

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

| Direction | Everyday (Tatoeba) BLEU / chrF | Formal (Europarl) BLEU / chrF | Domain BLEU / chrF |
|-----------|-------------------------------|-------------------------------|--------------------|
| en → es | 39.9 / 61.2 | 31.5 / 58.0 | 66.7 / 83.9 |
| en → fr | 42.6 / 61.1 | 28.1 / 56.7 | 71.7 / 85.9 |
| es → en | 44.8 / 62.8 | 31.4 / 57.8 | 67.8 / 81.2 |
| es → fr | 38.8 / 59.7 | 28.6 / 56.4 | 64.4 / 80.2 |
| fr → en | 47.9 / 63.6 | 30.0 / 56.8 | 71.4 / 82.6 |
| fr → es | 36.3 / 59.1 | 29.9 / 56.8 | 60.3 / 78.9 |
| **all** | **41.7 / 61.2** | **29.9 / 57.1** | **67.7 / 82.1** |

**Overall: BLEU 32.7, chrF 58.2.** Through the load-balanced API with automatic detection:
BLEU 32.7, chrF 58.1, source language correct for 99.4%.

Comparison on the same test set:

| Model | Tatoeba | Europarl | Domain | Overall BLEU / chrF |
|-------|---------|----------|--------|---------------------|
| Previous model (OPUS / Tatoeba downloads, 19 h)¹ | 38.4 | 28.4 | 59.8 | 30.8 / 56.8 |
| GitHub data, phase 1 (step 22,084) | 41.0 | 29.0 | 59.9 | 31.7 / 57.4 |
| **GitHub data, phase 2, shipped (step 26,881)** | **41.7** | **29.9** | **67.7** | **32.7 / 58.2** |

¹ The previous model may have seen some of these Tatoeba test sentences during its training,
so its Tatoeba score is, if anything, too high.

Quality: everyday and car-service sentences are mostly correct ("Our dealership is closed on
Monday" → "Notre concession est fermé lundi…", "al arrancar" → "when starting"), and names are
copied ("hi my name is chanakyha" → "Hola, me llamo Chanakyha."). Remaining errors are mostly
grammar slips (agreement, verb forms such as "Mary et Priya amener la voiture"). Long
parliament sentences are understandable but contain errors. Results are saved in
`training/logs/evaluate_final.txt` and `training/logs/score_api_final.txt`.

## 8. Reference files

### Training scripts and data definitions (`TranslatorService/training/`)

| File | Role |
|------|------|
| [download_data.py](../TranslatorService/training/download_data.py) | Step 1: downloads Tatoeba, Europarl and Universal Dependencies data from GitHub and converts it to TSV / text |
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
| `training/data/raw/github/` | download_data.py (download cache) | ~1.1 GB | no |
| `training/data/raw/parallel/`, `raw/mono/` | download_data.py (converted) | ~1 GB | no |
| `training/data/prepared/` | prepare_data.py | ~350 MB | no |
| `training/checkpoints/last.pt` | train_nmt.py (resumable state) | 126 MB | no |
| `models/nmt-en-es-fr/` | train_nmt.py (export) | 22 MB | **yes** |
| `models/lid/` | train_lid.py | 65 MB | **yes** |
| `training/logs/*.log`, `*.txt` | all scripts | small | yes (training history and scores) |

### Logs and results (`TranslatorService/training/logs/`)

| File | Contents |
|------|----------|
| `download_github.log` | What was downloaded from GitHub and the counts after conversion |
| `prepare.log` | Data counts, dev/test sizes, tokenizer |
| `train_lid.log` | LID training: accuracy per epoch and language, prior calibration, evaluation misses |
| `train_nmt.log` | Every 100 steps: loss, learning rate, speed. Every eval: dev BLEU per direction and sample translations. Both phases. |
| `evaluate_phase1.txt` | Test scores after phase 1 |
| `evaluate_final.txt` | Final test scores of the shipped model |
| `evaluate_old_opus_model_on_new_test.txt` | The previous (OPUS-trained) model on the same test set, for comparison |
| `score_api_final.txt` | Scores through the load-balanced API, with detection accuracy |
| `opus_v1/` | All logs of the previous, OPUS-trained models |

## 9. Troubleshooting

| Problem | Fix |
|---------|-----|
| `CUDA out of memory` | Lower `--max-tokens` (e.g. 2000) and raise `--accum` |
| Training very slow on GPU | Larger `--max-tokens` may overflow the GPU memory into shared RAM; use `--benchmark 50` to compare settings |
| Existing checkpoint gives garbage after `prepare_data.py` | The tokenizer changed. Always use `--reuse-tokenizer` when continuing an existing model. |
| Fine-tuned model is never exported | Use `--reset-best` when the dev BLEU of the new run may be lower than the old best |
| SSL errors in `download_data.py` | Certificates are not checked (`verify=False`), so this is a proxy problem: run `check_network.cmd` and pass the working proxy with `--proxy` |
| `download_data.py` reports `FAILED` | raw.githubusercontent.com or codeload.github.com is blocked: run `check_network.cmd` from the repository root, then rerun with `--proxy http://host:port` if a proxy works. Rerun (finished files are cached), or download the printed URLs in a browser into `training/data/raw/github/` (`--list` shows every URL and file name) |
| `absent ... (not published for this language)` | Normal: Tatoeba-Challenge has no dev or test file for some languages |
| `train_lid.py`: `mono/<lang>.txt not found` | Run `download_data.py` first |
| Starting a new run exports a weak model over the shipped one | `train_nmt.py` exports whenever dev BLEU improves, so the first evaluation of a new run replaces `models/nmt-en-es-fr/`. Copy the folder first if you want to keep it. |
| API still serves the old model | Restart the API (models are loaded at startup) |
