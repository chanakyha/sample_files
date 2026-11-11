# Offline Translator API

Fast, fully offline translation between the **24 official EU languages** with **automatic
source language detection**, served over a FastAPI REST API.

- Translation: [NLLB-200 distilled 600M](https://huggingface.co/facebook/nllb-200-distilled-600M),
  converted to [CTranslate2](https://github.com/OpenNMT/CTranslate2) with int8 weights (~600 MB)
- Detection: weighted vote of three detectors restricted to the 24 languages: `lingua`,
  fastText `lid.176` and `langid`. 96% correct on a 143-sentence test set of short phrases,
  sentences with foreign names, and casual text (a single detector: 80–90%)

> **Licence:** NLLB-200 is CC-BY-NC 4.0, **non-commercial use only**. Get clearance before
> using it in production. The fastText language-ID model is CC BY-SA 3.0.

| Code | Language | Code | Language | Code | Language |
|------|----------|------|----------|------|----------|
| bg | Bulgarian | fi | Finnish | mt | Maltese |
| cs | Czech | fr | French | nl | Dutch |
| da | Danish | ga | Irish | pl | Polish |
| de | German | hr | Croatian | pt | Portuguese |
| el | Greek | hu | Hungarian | ro | Romanian |
| en | English | it | Italian | sk | Slovak |
| es | Spanish | lt | Lithuanian | sl | Slovenian |
| et | Estonian | lv | Latvian | sv | Swedish |

## API

### `POST /translate`

`text` is an **array of arrays of sentences**. The response `translation` has exactly the same
shape. Each sentence's language is detected separately; a sentence already in `target_lang` is
returned unchanged.

Request:
```json
{
  "text": [
    ["The car needs a new battery.", "Please check the brakes."],
    ["La voiture est prête.", "Das ist gut."]
  ],
  "target_lang": "de"
}
```
Response:
```json
{
  "target_lang": "de",
  "translation": [
    ["Das Auto braucht eine neue Batterie.", "Bitte überprüfen Sie die Bremsen."],
    ["Das Auto ist bereit.", "Das ist gut."]
  ],
  "detected_lang": [["en", "en"], ["fr", "de"]],
  "confidence": [[0.775, 0.6908], [0.7604, 1.0]]
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `text` | yes | `string[][]`. Inner arrays may be empty; empty strings are returned as `""` |
| `target_lang` | yes | One of the 24 codes above |
| `source_lang` | no | Skip detection and treat every sentence as this language |
| `detect_mode` | no | `"sentence"` (default): detect each sentence, mixed languages OK. `"group"`: detect once per inner array from all its sentences; much more accurate for short sentences when each inner array is one language |

- Text with no letters (e.g. `"12345"`) gets `detected_lang: "und"` and is passed through.
- A string containing several sentences or line breaks is split internally and rejoined.
- Max sentences per request: 10,000 (`MAX_SENTENCES`), else **413**.

```powershell
curl.exe -X POST http://localhost:8000/translate -H "Content-Type: application/json" `
  -d '{\"text\": [[\"Bonjour le monde\", \"Merci\"]], \"target_lang\": \"en\"}'
```

### `GET /languages` — the 24 supported languages
### `GET /health` — status, model path, device, threads, beam size

## Performance

Measured on an 8-core laptop CPU, 48 distinct sentences en → de:

| Engine | Time |
|--------|------|
| PyTorch fp32, one sentence at a time | 160 s |
| PyTorch fp32, batched | 64 s |
| **CTranslate2 int8, parallel workers (current)** | **~8–11 s** |
| Same request repeated (cache) | 0.06 s |

How:
- **CTranslate2 int8**: C++ inference engine with quantised weights, much faster than PyTorch on CPU.
- **Parallel workers**: sentences are sorted by length, split into batches, and translated on
  several native threads at once (no Python GIL). Concurrent HTTP requests share the same pool.
- **Batching + de-duplication**: all sentences of a request go through one call; duplicates are
  translated once.
- **LRU cache**: repeated sentences return instantly.
- **Parallel detection**: lingua detects all sentences in parallel.

Tuning (environment variables):

| Variable | Default | Meaning |
|----------|---------|---------|
| `TRANSLATOR_BEAM_SIZE` | `2` | `1` = fastest (~35% faster), `4` = slightly better quality |
| `TRANSLATOR_INTER_THREADS` | CPU cores / 2 | Number of parallel translation workers |
| `TRANSLATOR_INTRA_THREADS` | `2` | CPU threads per worker |
| `TRANSLATOR_MAX_BATCH_SIZE` | `16` | Sentences per batch |
| `TRANSLATOR_CACHE_SIZE` | `20000` | Cached sentences (`0` disables) |
| `TRANSLATOR_DEVICE` | auto | `cpu` or `cuda` (GPU used automatically only if it supports fp16/int8) |
| `PRELOAD_MODELS` | `1` | Load the model at startup |

Run a single uvicorn worker: the model's own thread pool already uses every core, and each extra
`--workers` process would load another copy of the model.

## Fully offline

The API never touches the network. Models are loaded only from the `models/` folder (override
with `TRANSLATOR_MODEL_DIR`), and Hugging Face offline mode is forced on at import time:

```
models/
  nllb-200-distilled-600M-ct2/   translation (~600 MB)
  fasttext-lid/lid.176.bin       language detection (125 MB; lid.176.ftz 1 MB also works)
```

If the translation model is missing, `/translate` returns **503**. If the fastText file is
missing, detection still works using lingua + langid only, but less accurately.

## Setup

```powershell
git clone <repo-url>
cd Translator\TranslatorService
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Then get the model, either:

- **Build it (needs internet once):**
  ```powershell
  pip install -r requirements-convert.txt   # adds PyTorch, needed only for conversion
  python download_models.py                 # downloads ~2.6 GB, keeps ~725 MB
  ```
  Add `--compact-lid` to use the 1 MB language-ID model (2 fewer correct on the test set).
- **Or copy it** from a machine that already has it (USB, network share, OneDrive, or a GitHub
  Release asset):
  ```powershell
  Compress-Archive -Path models\* -DestinationPath nllb-ct2.zip
  # on the other machine, inside the project folder:
  Expand-Archive nllb-ct2.zip -DestinationPath models
  ```

The model (`models/`) and virtual environment (`.venv/`) are not in git (see `.gitignore`).

### Installing on a machine with no internet at all

On a connected machine (same OS and Python version as the target):
```powershell
pip download -r requirements.txt -d wheels
```
Copy the code, `wheels/` and the model zip to the offline machine, then:
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install --no-index --find-links wheels -r requirements.txt
Expand-Archive nllb-ct2.zip -DestinationPath models
```

## Run

```powershell
uvicorn app:app --host 0.0.0.0 --port 8000
```

Interactive docs: http://localhost:8000/docs

## Limitations

- Very short text in closely related languages can still be confused (Danish/Swedish,
  Croatian/Slovenian, Estonian/Finnish), e.g. `"vi ses i morgen"`. Use `detect_mode: "group"`
  or pass `source_lang` when you can.
- NLLB-600M quality is lower for Irish and Maltese than for the larger languages.

## Tests

```powershell
pytest -v
```
