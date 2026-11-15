# Setup and run guide

This guide takes you from an empty Windows PC to a working translator: the Python API running
and the desktop app connected to it. It takes about 15 minutes, most of it downloading Python
packages. Translation and language detection run fully offline after setup.

For more detail on each part, see [PYTHON_API_SETUP.md](PYTHON_API_SETUP.md) (API),
[DOTNET_APP_SETUP.md](DOTNET_APP_SETUP.md) (desktop app) and
[MODEL_TRAINING.md](MODEL_TRAINING.md) (retraining the models).

---

## 1. What you need

| Item | Version | Where to get it |
|------|---------|-----------------|
| Windows 10 or 11, 64-bit | | 4 CPU cores and 8 GB RAM recommended |
| Git | any | https://git-scm.com/download/win |
| Python | 3.11 or newer (3.14.7 tested) | https://www.python.org/downloads/ (tick **Add python.exe to PATH** during install) |
| .NET SDK | **10.0.x** | https://dotnet.microsoft.com/download/dotnet/10.0 (only needed to build the desktop app) |
| Disk space | about 2 GB | Python packages ~1.3 GB, models 86 MB, app build ~150 MB |

No GPU is needed. Check the tools from a new PowerShell window:

```powershell
git --version
python --version        # 3.11 or newer
dotnet --list-sdks      # must list a 10.0.x SDK
```

Do not build with only the .NET 11 SDK installed: it can no longer compile .NET 5 WPF projects.
The repository pins the .NET 10 SDK in [global.json](../global.json).

## 2. Get the code

```powershell
git clone https://github.com/chanakyha/sample_files.git Translator
cd Translator
```

The trained models are part of the repository (`TranslatorService/models/`), so there is
nothing else to download.

## 3. Set up the API (once)

```powershell
cd TranslatorService
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install -r requirements.txt
cd ..
```

This installs FastAPI, Uvicorn, PyTorch (CPU build), SentencePiece, NumPy, httpx and pytest into
`TranslatorService\.venv`.

**If pypi.org is blocked on your network**, download the packages on a machine with internet
access, copy the `wheels` folder over, and install from it:

```powershell
# on the machine with internet, in TranslatorService
pip download -r requirements.txt -d wheels
# on the target machine, in TranslatorService
python -m venv .venv
.venv\Scripts\pip install --no-index --find-links wheels -r requirements.txt
```

Both machines need the same Python version.

## 4. Build the desktop app (once)

From the repository root:

```powershell
dotnet build TranslatorDesktop
```

Expected ending: `Build succeeded. 0 Warning(s) 0 Error(s)`. The app is written to
`TranslatorDesktop\bin\Debug\net5.0-windows\TranslatorDesktop.exe`.

To run it, the PC needs a .NET Desktop Runtime, version 5 or newer (the app rolls forward to
newer versions). To make a copy that runs on PCs without .NET, see
[section 8](#8-standalone-desktop-app-for-other-pcs).

## 5. Run

The API and the app are separate programs. Start the API first.

**Window 1: the API** (4 instances behind a load balancer; use about one instance per 1–2 CPU
cores):

```powershell
TranslatorService\start_cluster.bat 4
```

Wait about 10 seconds for the models to load. Leave this window open; **Ctrl+C** stops the
API. For a single instance instead of a cluster, run `TranslatorService\start_api.bat`.

**Window 2: the desktop app:**

```powershell
TranslatorDesktop\bin\Debug\net5.0-windows\TranslatorDesktop.exe
```

The status bar shows `Ready · offline · CPU · …` once it is connected. If the API is not running
yet, it shows `Translation service not running` and connects by itself when the API starts.

### Using the app

1. The list starts with six sample sentences. Add your own text in the box at the bottom
   (press **Enter** or click **Add**). Any of the 24 EU languages can be added.
2. Hover over **Translate** in the top bar and click **English**, **French** or **Spanish**. Every
   row is translated into that language.
3. Hover over a row to see which language it was translated from and its original text.
4. Rows already in the target language stay unchanged. Rows in other languages (e.g. German) are
   detected but shown unchanged, marked "not supported": the model translates only between
   English, Spanish and French.
5. **Show original** switches back to the original texts. **Remove** and **Clear all** delete
   rows.

Closing the app does not stop the API.

## 6. Check that it works

With the API running, in another PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health        # status: ok, model_loaded: True
Invoke-RestMethod http://127.0.0.1:8000/lb/status     # cluster only: healthy 4 of 4

$body = '{"text":[["El motor hace un ruido extraño al arrancar."]],"target_lang":"en"}'
(Invoke-RestMethod http://127.0.0.1:8000/translate -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes($body))).translation
# The engine makes a strange noise when starting.
```

Interactive API documentation is at http://127.0.0.1:8000/docs.

Optional test runs, from `TranslatorService`:

| Command | Needs the API running | Expected result |
|---------|-----------------------|-----------------|
| `.venv\Scripts\python -m pytest -q` | no | `38 passed` |
| `.venv\Scripts\python tools\score_api.py` | yes | BLEU 32.7, chrF 58.1, detection 99.4% (takes a few minutes) |
| `.venv\Scripts\python tools\loadtest.py` | yes | 0 errors |

## 7. Use it from other PCs

On the PC running the API, allow other machines to connect, then open port 8000 in Windows
Firewall:

```powershell
$env:TRANSLATOR_HOST = "0.0.0.0"     # in cmd.exe: set TRANSLATOR_HOST=0.0.0.0
TranslatorService\start_cluster.bat 4
```

On the other PC, point the app at that machine:

```powershell
TranslatorDesktop.exe --url http://<api-pc-name>:8000
```

Setting the environment variable `TRANSLATOR_URL` instead of `--url` also works.

## 8. Standalone desktop app for other PCs

This build includes the .NET 5 runtime, so the target PC needs nothing installed:

```powershell
dotnet publish TranslatorDesktop -c Release -r win-x64 --self-contained true -o publish
```

Copy the whole `publish` folder (about 150 MB) and run `publish\TranslatorDesktop.exe`.

## 9. Retraining the models (optional)

The shipped models are ready to use. To retrain them (all data is downloaded from GitHub only;
about 1.2 GB of downloads, then hours of training, ideally on an NVIDIA GPU), follow
[MODEL_TRAINING.md](MODEL_TRAINING.md). The short version, from `TranslatorService`:

```powershell
.venv\Scripts\pip install -r requirements-train.txt
.venv\Scripts\python training\download_data.py
.venv\Scripts\python training\prepare_data.py
.venv\Scripts\python training\train_lid.py
.venv\Scripts\python training\train_nmt.py --hours 10
.venv\Scripts\python training\evaluate_nmt.py
```

## 10. Troubleshooting

| Problem | Fix |
|---------|-----|
| `Python environment not found` from `start_cluster.bat` | Do step 3 in `TranslatorService` |
| `pip install` fails with connection or SSL errors | pypi.org is blocked: use the wheels method in step 3 |
| `error CS2001: ... MainWindow.g.cs could not be found` | You are building with the .NET 11 SDK; install the .NET 10 SDK and keep `global.json` |
| `You must install .NET to run this application` | Install a .NET Desktop Runtime (5 or newer), or use the standalone build (section 8) |
| App status stays at `Translation service not running` | Start the API (step 5); check the `--url` address and the firewall |
| Port 8000 already in use | `TranslatorService\.venv\Scripts\python TranslatorService\cluster.py --instances 4 --port 8080`, then start the app with `--url http://127.0.0.1:8080` |
| `/health` shows `model_missing` | `TranslatorService\models\nmt-en-es-fr\` or `models\lid\` is missing; get them again from git |
| A row says "not supported" | It was detected as a language other than English, Spanish or French; only those three are translated |
| Slow with many users | Start more instances (`start_cluster.bat 6`), or set `TRANSLATOR_BEAM_SIZE=1` (about twice as fast, slightly worse quality) |

Logs are in `TranslatorService\logs\` (one JSON line per request; the translated text itself is
never logged).
