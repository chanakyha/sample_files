# EU Translator

Offline translation between English, Spanish and French with automatic language detection
(24 EU languages), using models trained from scratch on public datasets.

| Folder | What it is |
|--------|------------|
| [TranslatorService/](TranslatorService/) | Python REST API with our own trained models (Transformer translation en/es/fr, language detection for 24 languages). See its [README](TranslatorService/README.md). |
| [TranslatorDesktop/](TranslatorDesktop/) | Windows desktop app (.NET 5 WPF) that uses the API. Runs separately from the API. |
| [docs/](docs/) | Full documentation (see below) |

## Documentation

| Document | Contents |
|----------|----------|
| [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md) | **Start here:** step-by-step setup and running, from a new PC to a working translator |
| [docs/PYTHON_API_SETUP.md](docs/PYTHON_API_SETUP.md) | Install and run the Python API: single instance or load-balanced cluster, API reference, configuration, logs, tests, troubleshooting |
| [docs/DOTNET_APP_SETUP.md](docs/DOTNET_APP_SETUP.md) | Build and run the .NET 5 desktop app, standalone build, usage, how it connects, troubleshooting |
| [docs/MODEL_TRAINING.md](docs/MODEL_TRAINING.md) | Training both models from scratch: datasets (downloaded from GitHub only), every script and reference file, settings, exact training history, scores |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Running logic: request flow, load balancer, supervisor, desktop app, logging |

Quick start. You need Git and Python 3.11+; setup downloads the .NET 5 SDK itself, and the
trained models are included:

```powershell
.\check_network.cmd                       # optional: which download sites are blocked
.\setup.cmd                               # Python environment + .NET 5 SDK + app build
TranslatorService\start_cluster.bat 4     # window 1: API on http://127.0.0.1:8000
.\run_desktop.cmd                         # window 2: desktop app on .NET 5
```

Behind a corporate proxy, add `--proxy http://host:port` to `check_network.cmd`, `setup.cmd` and
`download_data.cmd`. Step by step: [docs/SETUP_GUIDE.md](docs/SETUP_GUIDE.md).

| Script | What it does |
|--------|--------------|
| [check_network.cmd](check_network.cmd) | Tests whether each download site is reachable or blocked, and why |
| [setup.cmd](setup.cmd) / [setup.ps1](setup.ps1) | Creates `TranslatorService\.venv`, installs the packages, installs the .NET 5 SDK into `.dotnet\`, builds the app. Options: `--proxy`, `--training`, `--gpu`, `--wheels`, `--runtests` |
| [run_desktop.cmd](run_desktop.cmd) | Starts the desktop app on the .NET 5.0.17 runtime |
| [download_data.cmd](download_data.cmd) | Downloads the training data from GitHub (only for retraining); `--proxy` |

## Desktop app

Plain standard Windows controls (menu, list, buttons), no custom styling.

- A list of texts in any EU language; add more with the text box at the bottom (Enter or **Add**).
- **Translate** in the top bar: hover to see the languages the model can translate into
  (English, French, Spanish); click one to translate every row into it. Rows detected as another
  language (e.g. German) are left unchanged and marked "not supported". The chosen language is ticked and becomes the target of the **Translate →** button.
- Each row shows the (translated) text and a **Remove** button. **Hover over a row** to see a
  tooltip with the languages and the original text, e.g. "Translated from Spanish to French /
  Original: El motor hace un ruido extraño al arrancar." **Show original** switches back.
- While a translation is shown, newly added rows are translated immediately.

The API and the desktop app are **separate programs**. Start the API first (it can run on this
machine or on a server); the app connects to it over HTTP. The status bar shows
"Translation service not running" until the API is reachable and reconnects automatically
when the API is started or restarted. Closing the app does not stop the API.

### 1. Run the API

Set up once with `.\setup.cmd`, then:

```powershell
TranslatorService\start_cluster.bat 4      # 4 instances behind a load balancer on :8000
TranslatorService\start_api.bat            # or: a single instance on :8000
```

Both listen on `http://127.0.0.1:8000`; Ctrl+C stops them. The cluster spreads requests over
the instances, restarts crashed ones and writes JSON logs to `TranslatorService\logs\`. See
[TranslatorService/README.md](TranslatorService/README.md#scaling-load-balancer) for details.

Set `TRANSLATOR_HOST=0.0.0.0` before starting it to accept connections from other machines.

### 2. Run the desktop app

Targets **.NET 5** (`net5.0-windows`) and is built with the **.NET 5 SDK 5.0.408**, pinned in
[global.json](global.json). `setup.cmd` installs that SDK, with its 5.0.17 runtime, into
`.dotnet\` and builds the app:

```powershell
.\run_desktop.cmd                              # runs on the .NET 5.0.17 runtime in .dotnet\
.\run_desktop.cmd --url http://server:8000
```

Started directly (`TranslatorDesktop\bin\Debug\net5.0-windows\TranslatorDesktop.exe`), it runs
on the PC's .NET Desktop Runtime 5 or newer (`RollForward=Major`). .NET 5 itself is out of
support since May 2022.

### Build a standalone .exe (includes the .NET 5 runtime, nothing to install)

```powershell
$env:DOTNET_ROOT = "$PWD\.dotnet"; $env:DOTNET_MULTILEVEL_LOOKUP = "0"
.dotnet\dotnet publish TranslatorDesktop -c Release -r win-x64 --self-contained true -o publish
```

`publish\TranslatorDesktop.exe` can be copied to any Windows 10/11 x64 PC (about 150 MB folder).

| Setting | Default | Meaning |
|---------|---------|---------|
| `--url` argument or `TRANSLATOR_URL` | `http://127.0.0.1:8000` | Address of the running API |
