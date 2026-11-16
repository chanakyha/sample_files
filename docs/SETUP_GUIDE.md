# Setup and run guide

This guide takes you from an empty Windows PC to a working translator: the Python API running
and the desktop app (.NET 5) connected to it. One script, `setup.cmd`, does the whole setup in
about 10–15 minutes, mostly downloading. Translation and language detection run fully offline
afterwards.

For more detail on each part, see [PYTHON_API_SETUP.md](PYTHON_API_SETUP.md) (API),
[DOTNET_APP_SETUP.md](DOTNET_APP_SETUP.md) (desktop app) and
[MODEL_TRAINING.md](MODEL_TRAINING.md) (retraining the models).

| Script (repository root) | What it does |
|--------------------------|--------------|
| [check_network.cmd](../check_network.cmd) | Tests whether the download sites are reachable or blocked |
| [setup.cmd](../setup.cmd) / [setup.ps1](../setup.ps1) | Sets up the Python environment, installs the .NET 5 SDK, builds the desktop app |
| [run_desktop.cmd](../run_desktop.cmd) | Starts the desktop app on the .NET 5 runtime |
| [download_data.cmd](../download_data.cmd) | Downloads the training data (only for retraining) |
| `TranslatorService\start_cluster.bat` | Starts the API |

All scripts that download something accept `--proxy http://host:port`.

---

## 1. What you need

| Item | Version | Notes |
|------|---------|-------|
| Windows 10 or 11, 64-bit | | 4 CPU cores and 8 GB RAM recommended; no GPU needed |
| Git | any | https://git-scm.com/download/win |
| Python | 3.11 or newer (3.14.7 tested) | https://www.python.org/downloads/; tick **Add python.exe to PATH** during install |
| Disk space | about 2.5 GB | Python packages ~1.3 GB, .NET 5 SDK ~500 MB, models 86 MB |

You do **not** need to install .NET yourself. The desktop app targets .NET 5, and `setup.cmd`
downloads the .NET 5 SDK (5.0.408, the last .NET 5 release, with runtime 5.0.17) into the
repository's `.dotnet\` folder. No administrator rights are needed, and .NET versions already
installed on the PC are not changed or used.

.NET 5 has been out of support from Microsoft since May 2022, so it gets no security updates.

## 2. Check your network

A corporate network may block some download sites. Run the check from the repository root
(after step 3; or download just `check_network.cmd` first):

```powershell
.\check_network.cmd                                         # direct connection
.\check_network.cmd --proxy http://proxy.yourcompany.com:8080
.\check_network.cmd --proxy http://user:password@proxy.yourcompany.com:8080
```

| Group | Sites | Needed for |
|-------|-------|------------|
| Set up and run | github.com, pypi.org, files.pythonhosted.org, builds.dotnet.microsoft.com | `git clone`, Python packages, .NET 5 SDK |
| Retrain the models | raw.githubusercontent.com, codeload.github.com | `download_data.cmd` |
| Optional | api.nuget.org, download.pytorch.org, www.python.org, dotnet.microsoft.com | standalone app build, GPU PyTorch, installers |
| Information only | opus.nlpl.eu, object.pouta.csc.fi, www.kaggle.com | old dataset sources, no longer used |

Each site is shown as `OK` or `BLOCKED` with the reason: DNS blocked, connection refused,
timed out, refused by the proxy, proxy login needed, or certificate not trusted because the
network inspects HTTPS. The script also shows the proxy configured in Windows, which is
usually the one to pass to `--proxy`. Its exit code is the number of required sites that
failed (0 = all fine).

If a site stays blocked even through the proxy, ask IT to allow it, or do that step on another
network. Python packages can also be installed from a folder of downloaded files (step 4).

## 3. Get the code

```powershell
git clone https://github.com/chanakyha/sample_files.git Translator
cd Translator
```

Behind a proxy, set it for git first:
`git config --global http.proxy http://proxy.yourcompany.com:8080`.

The trained models are part of the repository (`TranslatorService\models\`).

Keep the folder path short (for example `D:\Translator`). The .NET SDK contains deeply nested
files, and paths longer than about 90 characters before `.dotnet\` can hit the Windows
260-character path limit.

## 4. Run the setup

From the repository root, in cmd or PowerShell:

```powershell
.\setup.cmd
.\setup.cmd --proxy http://proxy.yourcompany.com:8080        # behind a proxy
```

What it does:

1. **Python**: finds Python 3.11+, creates `TranslatorService\.venv`, installs
   [requirements.txt](../TranslatorService/requirements.txt) (FastAPI, Uvicorn, PyTorch CPU,
   SentencePiece, NumPy, httpx, pytest) and checks that the packages import. It also warns if a
   model file is missing.
2. **.NET 5**: downloads the .NET 5.0.408 SDK from Microsoft (about 190 MB), checks it against
   Microsoft's published SHA-512 hash, unpacks it into `.dotnet\`, and builds
   `TranslatorDesktop` with it. [global.json](../global.json) pins this SDK version.

It ends with `Setup finished.` Running it again is safe: finished steps are skipped.

| Option | Meaning |
|--------|---------|
| `--proxy URL` | Proxy for all downloads (pip, .NET SDK); `http://user:password@host:port` for a proxy login |
| `--proxyusedefaultcredentials` | Sign in to the proxy with your Windows account (for the .NET SDK download) |
| `--training` | Also install the training packages ([requirements-train.txt](../TranslatorService/requirements-train.txt)) |
| `--gpu` | Also create `TranslatorService\.venv-gpu` with PyTorch for NVIDIA GPUs (CUDA 12.6, ~2.5 GB) |
| `--wheels FOLDER` | Install Python packages from a folder of wheel files, without pypi.org |
| `--runtests` | Run the Python tests (38) at the end |
| `--recreate` | Delete and recreate `TranslatorService\.venv` |
| `--skippython`, `--skipdotnet` | Skip that part |

In PowerShell you can also call `.\setup.ps1 -Proxy http://... -Training` directly;
`Get-Help .\setup.ps1 -Detailed` shows the same help.

**If pypi.org is blocked**, download the packages on a PC with internet access and the same
Python version, copy the folder over, and install from it:

```powershell
# on the PC with internet, in TranslatorService
python -m pip download -r requirements.txt -d D:\wheels
# on the target PC, in the repository root
.\setup.cmd --wheels D:\wheels
```

**If builds.dotnet.microsoft.com is blocked**, copy
`dotnet-sdk-5.0.408-win-x64.zip` from another PC into your `%TEMP%` folder and run setup
again. It uses that file once its SHA-512 hash matches. You can download the file from
https://builds.dotnet.microsoft.com/dotnet/Sdk/5.0.408/dotnet-sdk-5.0.408-win-x64.zip.

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
.\run_desktop.cmd
```

`run_desktop.cmd` starts `TranslatorDesktop\bin\Debug\net5.0-windows\TranslatorDesktop.exe` on
the .NET 5.0.17 runtime in `.dotnet\`. Starting the exe directly also works if a .NET Desktop
Runtime 5 or newer is installed on the PC; it then runs on that one.

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
.\run_desktop.cmd --url http://<api-pc-name>:8000
```

Setting the environment variable `TRANSLATOR_URL` instead of `--url` also works.

## 8. Standalone desktop app for other PCs

This build includes the .NET 5.0.17 runtime, so the target PC needs nothing installed. After
setup, from the repository root (needs api.nuget.org the first time, for the runtime packs):

```powershell
$env:DOTNET_ROOT = "$PWD\.dotnet"; $env:DOTNET_MULTILEVEL_LOOKUP = "0"
.dotnet\dotnet publish TranslatorDesktop -c Release -r win-x64 --self-contained true -o publish
```

Copy the whole `publish` folder (about 150 MB) and run `publish\TranslatorDesktop.exe`.

## 9. Retraining the models (optional)

The shipped models are ready to use. Retraining downloads about 1.1 GB of data from GitHub and
then trains for hours, ideally on an NVIDIA GPU. Full instructions are in
[MODEL_TRAINING.md](MODEL_TRAINING.md). The short version:

```powershell
.\setup.cmd --training                        # add --gpu for an NVIDIA GPU
.\download_data.cmd                           # or: .\download_data.cmd --proxy http://host:port
cd TranslatorService
.venv\Scripts\python training\prepare_data.py
.venv\Scripts\python training\train_lid.py
.venv\Scripts\python training\train_nmt.py --hours 10
.venv\Scripts\python training\evaluate_nmt.py
```

`download_data.cmd` downloads into `TranslatorService\training\data\raw\` and converts the
files (about 10 minutes in total on a fast connection). Files already downloaded are kept, so
if it fails halfway, run it again and it continues. `download_data.cmd --list` prints every
download address, for example to ask IT to allow them.

## 10. Troubleshooting

| Problem | Fix |
|---------|-----|
| `Python 3.11 or newer was not found` | Install Python (tick **Add python.exe to PATH**), open a new window, run setup again |
| `SETUP FAILED: installing requirements.txt` | pypi.org blocked: run `.\check_network.cmd`, then `.\setup.cmd --proxy ...` or `--wheels` (step 4) |
| `could not download ... dotnet-sdk-5.0.408` | builds.dotnet.microsoft.com blocked: use `--proxy`, or copy the zip into `%TEMP%` (step 4) |
| `does not match Microsoft's published SHA-512 hash` | The download was damaged or replaced by a proxy; it is deleted, run setup again |
| `could not unpack the .NET SDK` | The repository path is too long; move it to a short path such as `D:\Translator` |
| `the desktop app is running from the build folder` | Close the app, run setup again |
| `A compatible .NET SDK was not found` | You ran the PC-wide `dotnet build`, which has no 5.0.408 SDK. Use `setup.cmd`, or `.dotnet\dotnet build TranslatorDesktop` |
| `Python environment not found` from `start_cluster.bat` | Run `.\setup.cmd` |
| App status stays at `Translation service not running` | Start the API (step 5); check the `--url` address and the firewall |
| Port 8000 already in use | `TranslatorService\.venv\Scripts\python TranslatorService\cluster.py --instances 4 --port 8080`, then `.\run_desktop.cmd --url http://127.0.0.1:8080` |
| `/health` shows `model_missing` | `TranslatorService\models\nmt-en-es-fr\` or `models\lid\` is missing; get them again from git |
| A row says "not supported" | It was detected as a language other than English, Spanish or French; only those three are translated |
| `DOWNLOAD FAILED` from `download_data.cmd` | Run it again (it continues); if a site is blocked, use `--proxy` or see `.\check_network.cmd` |
| Slow with many users | Start more instances (`start_cluster.bat 6`), or set `TRANSLATOR_BEAM_SIZE=1` (about twice as fast, slightly worse quality) |

Logs are in `TranslatorService\logs\` (one JSON line per request; the translated text itself is
never logged).
