# Offline Translator API

Offline translation between **English, Spanish and French** (all 6 directions) with automatic
language detection for the **24 official EU languages**, served over a FastAPI REST API.

Both models are **our own**, trained from scratch on public datasets with the scripts in
[../TranslatorTraining/](../TranslatorTraining/) (training data, scripts, scores). No pretrained
or Hugging Face models are used. This folder holds only what the server needs to run.

| Model | What | Size |
|-------|------|------|
| Translation (`models/nmt-en-es-fr/`) | Transformer encoder-decoder, 6+2 layers, 11M parameters, own SentencePiece tokenizer (16k) | 21 MB |
| Language detection (`models/lid/`) | fastText-style classifier over hashed words + character n-grams, 24 languages | 64 MB |

Code: [translator/nmt.py](translator/nmt.py) (model, beam search),
[translator/lid.py](translator/lid.py) (detector), [translator/engine.py](translator/engine.py)
(batching, cache), [app.py](app.py) (API).

## Setup

`setup.cmd --skipdotnet` in the repository root creates `.venv` here and installs
[requirements.txt](requirements.txt). By hand: `python -m venv .venv`, then
`.venv\Scripts\pip install -r requirements.txt`.

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

## Bundled exe (no Python needed)

`build_server_exe.cmd` in the repository root builds the server with PyInstaller into
`dist/TranslatorServer/`: `TranslatorServer.exe`, `_internal/` (Python, PyTorch, FastAPI) and a copy
of `models/` (about 580 MB in total). Copy that folder to any Windows 10/11 x64 PC and run:

```powershell
TranslatorServer.exe                          # cluster: 2 instances + load balancer on :8000
TranslatorServer.exe --instances 4 --host 0.0.0.0
TranslatorServer.exe --single --port 8000     # one instance
```

[server_main.py](server_main.py) is the entry point. In the exe, the cluster supervisor starts
each instance as `TranslatorServer.exe worker --port 800i` and the load balancer as
`TranslatorServer.exe balancer ...`; the API code is the same as when it runs from source.
[TranslatorServer.spec](TranslatorServer.spec) is the PyInstaller recipe. Logs go to `logs/`
next to the exe.

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
`logs/loadtest.jsonl`. These numbers come from a 4-core processor, beam 4, with
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
```

[../TranslatorTraining/score_api.py](../TranslatorTraining/score_api.py) sends the whole test set through the running API with automatic
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
| `TRANSLATOR_MODEL_DIR` | `./models` (exe: `models\` next to it) | Where the models are |
| `PRELOAD_MODELS` | `1` | Load models at startup |

## Moving to another machine

Easiest: copy the bundled exe folder (above). From source:

`models/` is only 85 MB: copy it (zip, USB, network share) next to the code. Runtime needs only
`pip install -r requirements.txt` (FastAPI, PyTorch, SentencePiece); for a machine without
internet, see `pip download` below.

```powershell
pip download -r requirements.txt -d wheels        # on a connected machine
pip install --no-index --find-links wheels -r requirements.txt   # on the offline machine
```

## Limitations

- Translation quality is far below large pretrained systems: the model is small and trained for
  hours. Expect correct simple sentences and rough or wrong longer ones.
- Only en/es/fr can be translated; other detected languages are returned unchanged.
- Very short text in closely related languages can be confused (e.g. Croatian/Slovenian).

## Tests

```powershell
.venv\Scripts\python -m pytest -v     # 39 tests
```
