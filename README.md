# Offline Translator API

Fully offline translation between the **24 official EU languages** with **automatic source
language detection**, served over a FastAPI REST API.

- Translation: [NLLB-200 distilled 600M](https://huggingface.co/facebook/nllb-200-distilled-600M)
  (one multilingual model for all 552 directions)
- Detection: `lingua` + `langid` (models ship inside the pip packages), restricted to the 24 languages

> **Licence:** NLLB-200 is CC-BY-NC 4.0, **non-commercial use only**. Get clearance before
> using it in production.

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

## Fully offline

The API never touches the network. The model is loaded only from `models/nllb-200-distilled-600M`
(override the base folder with `TRANSLATOR_MODEL_DIR`), and Hugging Face offline mode is forced on
at import time. If the model is missing, `/translate` returns **503**.

## Setup (once, with internet)

```powershell
git clone <repo-url>
cd Translator
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python download_models.py          # downloads ~2.5 GB, stores 1.2 GB (fp16) in ./models
```

After this the machine can be disconnected permanently.

The model (`models/`) and virtual environment (`.venv/`) are **not** in git (see `.gitignore`):
the model is 1.2 GB, above GitHub's 100 MB file limit, and `.venv` only works on the
machine where it was created.

### Copying the model to another machine instead of downloading it

Zip the model folder, move it any way you like (USB drive, network share, OneDrive / Google Drive,
or as an asset on a GitHub Release, max 2 GB per file), and unzip into the project:
```powershell
Compress-Archive -Path models\nllb-200-distilled-600M -DestinationPath nllb-model.zip
# on the other machine, inside the project folder:
Expand-Archive nllb-model.zip -DestinationPath models
```

### Installing on a machine that has no internet at all

On a connected machine (same OS and Python version as the target):
```powershell
pip download -r requirements.txt -d wheels
Compress-Archive -Path wheels -DestinationPath wheels.zip
```
Copy the code, `nllb-model.zip` and `wheels.zip` to the offline machine, unzip both, then:
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install --no-index --find-links wheels -r requirements.txt
```

## Run

```powershell
uvicorn app:app --host 0.0.0.0 --port 8000
# optional: load the model at startup instead of on first request
$env:PRELOAD_MODELS = "1"; uvicorn app:app --port 8000
```

Interactive docs: http://localhost:8000/docs

## API

### `POST /translate`

The source language is detected automatically. If it already equals `target_lang`, the text is
returned unchanged.

Request:
```json
{ "text": "Das Auto braucht eine neue Batterie.", "target_lang": "en" }
```
Response:
```json
{
  "detected_lang": "de",
  "confidence": 0.98,
  "target_lang": "en",
  "input": "Das Auto braucht eine neue Batterie.",
  "translation": "The car needs a new battery."
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `text` | yes | A string, or a list of strings (each one is detected separately) |
| `target_lang` | yes | One of the 24 codes above |
| `source_lang` | no | Skip detection and use this source language (useful for one-word inputs) |

For a list input, `detected_lang`, `confidence` and `translation` are lists in the same order.
Text with no letters (e.g. `"12345"`) returns `detected_lang: "und"` and is passed through.
Multi-sentence text is split into sentences for better quality; line breaks are preserved.

```powershell
curl.exe -X POST http://localhost:8000/translate -H "Content-Type: application/json" `
  -d '{\"text\": \"Bonjour le monde\", \"target_lang\": \"de\"}'
```

### `GET /languages` — the 24 supported languages
### `GET /health` — status, device (cpu/cuda), model path, whether the model is loaded

## Limitations

- Detection on very short input (one or two words) can be wrong, e.g. `"Hello"` → Italian.
  Pass `source_lang` when you know it.
- NLLB-600M quality is lower for Irish and Maltese than for the larger languages.

## Tests

```powershell
pytest -v
```
