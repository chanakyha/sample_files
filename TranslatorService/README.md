# Offline Translator API

Offline translation between **English, Spanish and French** (all 6 directions) with automatic
language detection for the **24 official EU languages**, served over a FastAPI REST API.

Both models are **our own**, trained from scratch on public datasets with the scripts in
[training/](training/). No pretrained or Hugging Face models are used.

| Model | What | Size |
|-------|------|------|
| Translation (`models/nmt-en-es-fr/`) | Transformer encoder-decoder, 6+2 layers, 11M parameters, own SentencePiece tokenizer (16k) | 21 MB |
| Language detection (`models/lid/`) | fastText-style classifier over hashed words + character n-grams, 24 languages | 64 MB |

Code: [translator/nmt.py](translator/nmt.py) (model, beam search),
[translator/lid.py](translator/lid.py) (detector), [translator/engine.py](translator/engine.py)
(batching, cache), [app.py](app.py) (API).

## Training data (downloaded from GitHub only)

The original dataset hosts and Kaggle are blocked on the corporate network, so
[training/download_data.py](training/download_data.py) takes everything from public GitHub
repositories (github.com, codeload.github.com, raw.githubusercontent.com):

| Data | GitHub repository | Used for | Licence |
|------|-------------------|----------|---------|
| Tatoeba (official OPUS copy) | [Helsinki-NLP/Tatoeba-Challenge](https://github.com/Helsinki-NLP/Tatoeba-Challenge) | translation: everyday sentences; detection | CC BY 2.0 FR |
| Tatoeba en-fr / en-es | [viljarpuusepp/Attention](https://github.com/viljarpuusepp/Attention) (`fra.txt`), [doozan/spanish_data](https://github.com/doozan/spanish_data) (`sentences.tsv`) | translation: everyday sentences | CC BY 2.0 FR |
| Europarl (XML per session) | [UDS-SFB-B6-Datasets/Multilingual-Parallel-Direct-Europarl](https://github.com/UDS-SFB-B6-Datasets/Multilingual-Parallel-Direct-Europarl) | translation: formal text (300k pairs per language pair); detection (de, it, nl, pt) | no copyright restrictions |
| Universal Dependencies treebanks | [UniversalDependencies](https://github.com/UniversalDependencies) (28 repositories) | detection: sentences in all 24 languages | mostly CC BY-SA 4.0 (per treebank) |

Details, including how the Europarl speeches are aligned: [../docs/MODEL_TRAINING.md](../docs/MODEL_TRAINING.md).

## Training (needs GitHub access only for step 1)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-train.txt

python training\download_data.py      # ~1.1 GB from GitHub into training/data/raw
python training\prepare_data.py       # clean, split dev/test, train tokenizer, encode (~20 min)
python training\train_lid.py          # language detector (~45 min)
python training\train_nmt.py --hours 10
```

`train_nmt.py` scores the dev set every 30 minutes and exports the best model so far to
`models/nmt-en-es-fr/`, so the API can be used while training continues. To stop early, create
the file `training\STOP` (it saves and exits); continue later with `--resume`. Progress is in
`training\logs\train_nmt.log`.

On a 4-core laptop CPU (i5-1035G1) training runs at ~770 target tokens/s, about 7 s per step.
A GPU is much faster; the shipped model was trained on a GPU.

### Training on a GPU

Training (only training; the API runs on the CPU) can use an NVIDIA GPU. Install a CUDA build of
PyTorch into a separate environment so the runtime `.venv` stays small. CUDA 12.6 builds still
support older GPUs (e.g. GeForce MX130, compute capability 5.0). The shipped model:

```powershell
python -m venv .venv-gpu
.venv-gpu\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu126
.venv-gpu\Scripts\pip install sentencepiece numpy sacrebleu
# phase 1: 14 h
.venv-gpu\Scripts\python training\train_nmt.py --hours 14 --max-tokens 3000 --accum 2 `
    --mix "Tatoeba=3,Europarl=0.3,Domain=4,Names=1"
# phase 2: 3 h fine-tuning on the domain sentences
.venv-gpu\Scripts\python training\train_nmt.py --resume --new-epoch --reset-best --hours 3 `
    --max-tokens 3000 --accum 2 --mix "Tatoeba=1,Europarl=0.15,Domain=8,Names=1"
```

`--device auto` (default) picks the GPU when available. `--max-tokens` × `--accum` is the batch
size per step; lower `--max-tokens` if the GPU runs out of memory. `--mix` weights the corpora:
the short everyday Tatoeba sentences are repeated, the long parliament text is subsampled.

### Results

Language detection (held-out sentences, up to 500 per language): **98.8%** (Slovenian 92%, all
others 98–100%). Challenge set of 143 short phrases, sentences with foreign names and casual
text in many languages ([training/lid_challenge.tsv](training/lid_challenge.tsv)): **139/143**.
Everyday English/French/Spanish phrases, greetings and car-service sentences
([training/lid_everyday.tsv](training/lid_everyday.tsv)): **146/150**; a second set of casual
phrases ([training/lid_everyday2.tsv](training/lid_everyday2.tsv)): **70/70**. The remaining
misses are single words that exist in several languages ("no", "non"; "hi" is Maltese for
"she") and short phrases of languages with little GitHub data ("Ďakujem pekne", "Köszönöm
szépen"). These sets are used only for evaluation: `train_lid.py` drops any line of
[training/lid_phrases.tsv](training/lid_phrases.tsv) (hand-written greetings and short chat
phrases in all 24 languages, used for training) that also occurs in an evaluation set.

The detector gives English, Spanish and French (the languages the translator supports) a small
log-prior of 1.5, chosen on held-out data so it costs the other 21 languages under 0.5%
accuracy; it only decides genuinely ambiguous short input.

Translation, held-out test set (500 sentences per direction and corpus, never seen in training;
beam 4). "Domain" is [training/domain_test.tsv](training/domain_test.tsv): 45 hand-written
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
([training/domain_data.py](training/domain_data.py), used by `prepare_data.py`):

* **Domain**: car-service, greeting and introduction sentences in en/es/fr, built from
  hand-written templates and word lists (parts, times, noises, names).
* **Names**: Tatoeba pairs with "Tom"/"Mary" replaced on both sides by random real or made-up
  names, so the model learns to copy names it has never seen (e.g. "Thirunavukkarasu").

Short everyday and car-service sentences are mostly correct; long parliament text is
understandable but has errors. `GET /health` shows the dev BLEU of the currently exported model;
rerun `python training\evaluate_nmt.py` after more training.

## Run

One instance:

```powershell
start_api.bat                 # = uvicorn app:app --host 127.0.0.1 --port 8000
```

Scaled out: several instances behind a load balancer (recommended for many users):

```powershell
start_cluster.bat 4           # = python cluster.py --instances 4 --port 8000
```

Interactive docs: http://localhost:8000/docs. Fully offline: only `models/` is read. Set
`TRANSLATOR_HOST=0.0.0.0` first to accept connections from other machines.

## Scaling: load balancer

```
client ──> loadbalancer.py :8000 ──> app.py :8001   (api-8001)
                                ├──> app.py :8002   (api-8002)
                                └──> app.py :800N   ...
```

[cluster.py](cluster.py) starts N API instances, each a separate process with its own model copy
and its own share of the CPU threads. It also starts [loadbalancer.py](loadbalancer.py) on the
public port and restarts any process that crashes. Only the load balancer port is used by
clients; the instances listen on 127.0.0.1.

- **Routing:** each request goes to the healthy instance with the fewest requests in progress
  (least connections, round-robin between equals).
- **Health checks:** `GET /health` on every instance every 2 s. An instance that stops
  answering is taken out of rotation and put back when it recovers.
- **Failover:** a request whose instance dies mid-request is retried on another instance.
  Translation has no side effects, so the retry is safe.
- **Status:** `GET /lb/status` lists the instances, their health, requests in progress, total
  requests, errors and average latency.

### Load test results

[tools/loadtest.py](tools/loadtest.py) sends requests of 10 new, random sentences each (no cache
hits) and records throughput, latency and errors. Results are appended to
`logs/loadtest.jsonl`. These numbers come from a 4-core laptop CPU, beam 4, with
`start_cluster.bat 4`.

| Test | Requests | Errors | Throughput | Latency p50 / p95 / max |
|------|---------:|-------:|-----------:|------------------------:|
| 1 user at a time | 20 | 0 | 35 sentences/s | 0.29 / 0.33 / 0.35 s |
| 8 concurrent | 32 | 0 | 61 sentences/s | 1.25 / 1.44 / 1.55 s |
| 16 concurrent | 64 | 0 | 61 sentences/s | 2.49 / 2.73 / 2.94 s |
| 64 concurrent | 256 | 0 | 62 sentences/s | 10.2 / 10.8 / 11.8 s |
| Spike: 200 requests at the same moment | 200 | 0 | 59 sentences/s | 17.5 / 32.1 / 34.0 s |
| One request with 500 / 1,000 / 2,000 sentences | 3 | 0 | 60–70 sentences/s | 7.2 / 14.9 / 33.2 s |
| Mixed: translate, health, languages, invalid input (422/404) | 120 | 0 unexpected | | |
| Soak: 16 concurrent for 5 min, then 4 more min | 3,239 | 0 | 60 sentences/s | 2.7 / 3.1 / 3.7 s |
| Chaos: 16 concurrent for 2.5 min, an instance killed every 20 s (6 kills) | 788 | 0 | 52 sentences/s | 3.1 / 3.9 / 6.2 s |

* **Scaling:** a single instance (`start_api.bat`) reaches 31 sentences/s at 16 and 64
  concurrent users (p50 5.0 s and 20.7 s). The 4-instance cluster doubles throughput and
  halves the latency. A single user alone is slightly faster on one instance (0.22 s), because
  it gets all CPU threads.
* **Failover:** in the chaos test, 24 requests that were running on a killed instance were
  retried on another one, so no request failed. All 4 instances were back within seconds.
* **Memory:** each instance rose from 600 MB to 635–665 MB in the first 5-minute soak, and by
  only 7–31 MB more in the next 4 minutes. The growth levels off; part of it is the per-instance
  translation cache (at most 20,000 sentences).
* **Limits:** the largest allowed request (10,000 sentences) takes about 3 minutes on one
  instance, within the load balancer's 10-minute timeout. Split big jobs into requests of a
  few hundred sentences so they spread over all instances.

```powershell
python tools\loadtest.py                                   # ramp, spike, large, mixed
python tools\loadtest.py --scenario soak --soak-seconds 300
python tools\loadtest.py --scenario chaos --chaos-seconds 120   # needs cluster.py
python tools\score_api.py                                  # BLEU/chrF through the API
```

`tools/score_api.py` sends the whole test set through the running API with automatic
language detection, as clients use it. Through the 4-instance cluster: **BLEU 32.7, chrF 58.1,
source language detected correctly for 99.4%** of the 6,270 sentences. This matches the
direct model evaluation, so the load balancer and detection do not lower quality.

## Logs

Every process writes JSON lines (one object per line, rotating at 10 MB × 5 files) to
`logs/` and readable lines to the console:

| File | Contents |
|------|----------|
| `logs/loadbalancer.log` | every request: request id, instance chosen, status, latency, retries; instances going up/down |
| `logs/api-8001.log`, … | every request (access log) plus, for `/translate`, sentence count, characters, target, detected languages, duration; model loading; errors with traceback |
| `logs/cluster.log` | processes started, crashed (exit code) and restarted |
| `logs/api.log` | the single instance from `start_api.bat` |

Each request gets an `X-Request-ID`. The load balancer creates it (or keeps the client's) and
passes it to the instance; it is returned in the response and appears in both logs:

```powershell
Select-String 97bbb2f9f2834aac logs\*.log
```

The text being translated is never logged, only counts and languages. Health checks are
logged at DEBUG level only.

| Variable | Default | Meaning |
|----------|---------|---------|
| `TRANSLATOR_LOG_DIR` | `./logs` | Where log files go |
| `TRANSLATOR_LOG_LEVEL` | `INFO` | `DEBUG` also logs health checks |
| `TRANSLATOR_INSTANCES` | `2` | Default instance count for `cluster.py` |

## API

### `POST /translate`

```json
{ "text": [["The car needs a new battery.", "Merci beaucoup."], ["Das ist gut."]], "target_lang": "es" }
```

| Field | Required | Description |
|-------|----------|-------------|
| `text` | yes | `string[][]`: arrays of sentences; the response has the same shape |
| `target_lang` | yes | `en`, `es` or `fr` |
| `source_lang` | no | Skip detection and treat every sentence as this language |
| `detect_mode` | no | `"sentence"` (default) or `"group"` (one detection per inner array; better for short sentences) |

Response fields: `translation`, `detected_lang`, `confidence`, and `translated` (false when a
sentence was returned unchanged: already in the target language, no letters, or detected as a
language the model cannot translate, e.g. German).

### `GET /languages`
`languages`: what the model translates (en, es, fr). `detectable`: the 24 detected languages.

### `GET /health`
Status, model path, dev BLEU and training step of the loaded model, threads, beam size.

### Settings (environment variables)

| Variable | Default | Meaning |
|----------|---------|---------|
| `TRANSLATOR_BEAM_SIZE` | `4` | `1` = greedy (fastest) |
| `TRANSLATOR_THREADS` | CPU cores / 2 | PyTorch threads |
| `TRANSLATOR_BATCH_SIZE` | `32` | Sentences per batch |
| `TRANSLATOR_CACHE_SIZE` | `20000` | Cached sentence translations (`0` disables) |
| `TRANSLATOR_MODEL_DIR` | `./models` | Where the models are |
| `PRELOAD_MODELS` | `1` | Load models at startup |

## Moving to another machine

`models/` is only 85 MB: copy it (zip, USB, network share) next to the code. Runtime needs only
`pip install -r requirements.txt` (FastAPI, PyTorch, SentencePiece); for a machine without
internet, see `pip download` below.

```powershell
pip download -r requirements.txt -d wheels        # on a connected machine
pip install --no-index --find-links wheels -r requirements.txt   # on the offline machine
```

## Limitations

- Translation quality is far below large pretrained systems: the model is small and trained for
  hours on a laptop CPU. Expect correct simple sentences and rough or wrong longer ones.
- Only en/es/fr can be translated; other detected languages are returned unchanged.
- Very short text in closely related languages can be confused (e.g. Croatian/Slovenian).

## Tests

```powershell
pytest -v
```
