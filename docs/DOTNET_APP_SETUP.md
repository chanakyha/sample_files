# .NET Desktop App: setup and running

`TranslatorDesktop` is a Windows desktop client (WPF, **.NET 5**) for the Translator API. It is
a **separate program**: it does not start or contain the Python API, it only connects to it
over HTTP. Start the API first ([PYTHON_API_SETUP.md](PYTHON_API_SETUP.md)).

---

## 1. Requirements

| Item | Version | Notes |
|------|---------|-------|
| Windows 10/11 x64 | | WPF is Windows-only |
| .NET SDK (to build) | **.NET 5 SDK 5.0.408** (the last .NET 5 release) | Pinned in [global.json](../global.json). [setup.cmd](../setup.cmd) downloads it into `.dotnet\` in the repository (no admin rights, PC-wide .NET installs untouched) |
| .NET runtime (to run) | **.NET 5.0.17 Desktop Runtime** (included in the SDK in `.dotnet\`) | [run_desktop.cmd](../run_desktop.cmd) starts the app on it. Started directly, the exe uses any installed Desktop Runtime 5 or newer (`RollForward=Major`). The standalone build (section 4) needs no runtime at all. |
| Visual Studio (optional) | 2019 16.8+ / 2022 | Open `Translator.slnx`; needs the .NET 5 SDK, see section 3 |
| The Translator API | running | Default address `http://127.0.0.1:8000` |

.NET 5 has been out of support since May 2022 (no security updates). The project targets it
on request. Moving to .NET 8 or later only needs a change of `TargetFramework` and
`global.json`.

### How setup installs the .NET 5 SDK

`setup.cmd` (or `setup.ps1`) checks for the 5.0.408 SDK in `.dotnet\` or on the PC. If it is
missing, it:

1. downloads `dotnet-sdk-5.0.408-win-x64.zip` (about 190 MB) from
   `https://builds.dotnet.microsoft.com/dotnet/Sdk/5.0.408/`, using `--proxy` if given;
2. checks its SHA-512 hash against the one Microsoft publishes in
   [releases.json](https://builds.dotnet.microsoft.com/dotnet/release-metadata/5.0/releases.json)
   and stops if they differ;
3. unpacks it into `.dotnet\`. The SDK contains runtime 5.0.17: Microsoft.NETCore.App,
   Microsoft.WindowsDesktop.App and Microsoft.AspNetCore.App.

To build only the app (Python part skipped): `.\setup.cmd --skippython`.

## 2. Project files

| File | Purpose |
|------|---------|
| [TranslatorDesktop/TranslatorDesktop.csproj](../TranslatorDesktop/TranslatorDesktop.csproj) | `net5.0-windows`, C# 9, WPF, `RollForward=Major` |
| [TranslatorDesktop/App.xaml](../TranslatorDesktop/App.xaml) / `App.xaml.cs` | Application entry, opens `MainWindow` |
| [TranslatorDesktop/MainWindow.xaml](../TranslatorDesktop/MainWindow.xaml) | Window layout: plain standard controls (Menu, ListView, TextBox, Buttons, StatusBar), no custom styles |
| [TranslatorDesktop/MainWindow.xaml.cs](../TranslatorDesktop/MainWindow.xaml.cs) | Logic: connection monitor, translate, add/remove rows, language menu |
| [TranslatorDesktop/TranslatorApi.cs](../TranslatorDesktop/TranslatorApi.cs) | HTTP client for `/health`, `/languages`, `/translate` (System.Text.Json with snake_case names) |
| [TranslatorDesktop/TextRow.cs](../TranslatorDesktop/TextRow.cs) | One row: original text, displayed text, detected / target language, tooltip text |
| [TranslatorDesktop/Languages.cs](../TranslatorDesktop/Languages.cs) | Names of the 24 EU languages |
| [global.json](../global.json) | Pins the .NET 5 SDK (5.0.408) |
| [setup.ps1](../setup.ps1) / [setup.cmd](../setup.cmd) | Installs the .NET 5 SDK into `.dotnet\` and builds the app (plus the Python setup) |
| [run_desktop.cmd](../run_desktop.cmd) | Starts the app on the .NET 5 runtime in `.dotnet\` |
| [Translator.slnx](../Translator.slnx) | Solution file for Visual Studio |

## 3. Build and run

From the repository root:

```powershell
.\setup.cmd --skippython          # installs the .NET 5 SDK if needed, then builds
.\run_desktop.cmd                 # starts the app on .NET 5.0.17
```

To build by hand with the SDK in `.dotnet\` (PowerShell):

```powershell
$env:DOTNET_ROOT = "$PWD\.dotnet"; $env:DOTNET_MULTILEVEL_LOOKUP = "0"
.dotnet\dotnet build TranslatorDesktop
```

`DOTNET_MULTILEVEL_LOOKUP=0` makes the SDK use only the runtimes in `.dotnet\`, not the ones
installed on the PC. The plain `dotnet` command uses the PC's own SDKs. Unless a 5.0.408 SDK is
installed there, it fails with "A compatible .NET SDK was not found", because global.json asks
for 5.0.408.

For Visual Studio, open `Translator.slnx` from a window where `DOTNET_ROOT` points at `.dotnet`
and `PATH` starts with it, or install the .NET 5.0.408 SDK on the PC.

### Connecting to the API

The API address is chosen in this order:

1. the command-line argument `--url`, e.g. `TranslatorDesktop.exe --url http://server01:8000`;
2. the environment variable `TRANSLATOR_URL`;
3. the default `http://127.0.0.1:8000`.

The address can point at a single API instance or at the load balancer of a cluster; the app
works the same with both.

## 4. Standalone build (no .NET installation needed)

```powershell
$env:DOTNET_ROOT = "$PWD\.dotnet"; $env:DOTNET_MULTILEVEL_LOOKUP = "0"
.dotnet\dotnet publish TranslatorDesktop -c Release -r win-x64 --self-contained true -o publish
```

`publish\TranslatorDesktop.exe` includes the .NET 5.0.17 runtime. Copy the whole `publish`
folder (about 150 MB, 502 files) to any Windows 10/11 x64 PC and run the exe. The first publish
downloads the runtime packs from api.nuget.org.

## 5. Using the app

| Element | What it does |
|---------|--------------|
| **Translate** menu (top) | Hover to open. Lists the languages the API can translate into (English, French, Spanish). Click one to translate all rows into it; it then stays selected (ticked). **Show original text** restores all rows. |
| List of texts | Six sample sentences at start. Each row shows the current text and a **Remove** button. |
| **Hover over a row** | Tooltip, e.g. `Translated from Spanish to French` / `Original: El motor hace un ruido extraño al arrancar.` Rows that were not changed say so: `Already in French; not translated`, `Detected German, which cannot be translated; shown unchanged`. |
| **Text** box + **Add** (or Enter) | Adds a row. While translations are shown, a new row is translated immediately. |
| **Translate → <language>** | Translates all rows into the selected language |
| **Clear all** / **Show original** | Remove all rows / show the original texts again |
| Status bar | Connection state, target language, time of the last translation |

Status bar messages:

| Message | Meaning |
|---------|---------|
| `Connecting to translation service at …` | Just started |
| `Ready · offline · CPU · 4 workers · 127.0.0.1:8000` | Connected; Translate is enabled |
| `Translation service not running at … (retrying…)` | API not reachable; Translate is disabled. The app keeps retrying every 2 s and reconnects by itself. |
| `Translation model missing …` | API is running but has no models |

## 6. How the app works

* **No Python inside.** The app never starts the API. Closing the app leaves the API running,
  and the API can be restarted while the app is open.
* **Connection monitor** (`MonitorServiceAsync`). It polls `GET /health` every 2 s while the API
  is down and every 5 s while it is up. When the API comes up, the app reads `GET /languages`,
  rebuilds the Translate menu and enables translation.
* **Translate.** All rows go to the API in one `POST /translate` request
  (`{"text": [[row1, row2, …]], "target_lang": "fr"}`). The response gives the translation and
  detected language of each row; these update the row and its tooltip.
* **Errors.** If a request fails because the API went away, the status bar says so and the
  monitor reconnects. Other API errors (e.g. 422) are shown in a message box.

## 7. Testing

The app was tested with Windows UI Automation (a PowerShell script that drives the real
window). The 35 checks cover:
* startup without the API, then connecting when the API starts later;
* the hover menu and the language list;
* translating to French, Spanish and English;
* tooltips, including a real mouse hover;
* adding rows (button and Enter), a German row marked "not supported", Remove, Clear all;
* stopping and restarting the API, with automatic reconnection;
* app traffic reaching both instances through the load balancer;
* the API still running after the app closes.

All 35 checks passed on the Debug build (built with the .NET 10 SDK, running on .NET 8 via
roll-forward) and on the standalone .NET 5.0.17 build. With the .NET 5.0.408 SDK, the app
builds with 0 warnings and runs on the .NET 5.0.17 runtime from `.dotnet\`. That runtime was
confirmed from the loaded `coreclr.dll` and WPF assemblies.

## 8. Troubleshooting

| Problem | Fix |
|---------|-----|
| `A compatible .NET SDK was not found` | The PC-wide `dotnet` was used. Run `.\setup.cmd --skippython`, or build with `.dotnet\dotnet` (section 3) |
| `could not download ... dotnet-sdk-5.0.408` | builds.dotnet.microsoft.com is blocked: `.\setup.cmd --proxy http://host:port`, or copy the zip into `%TEMP%` and run setup again |
| `could not unpack the .NET SDK` | Path too long: move the repository to a short path such as `D:\Translator` |
| `error CS2001: Source file '...MainWindow.g.cs' could not be found` | Built with the .NET 11 SDK, which cannot compile .NET 5 WPF projects. Use the .NET 5 SDK (section 3) |
| `You must install .NET to run this application` | Start it with `run_desktop.cmd`, install any .NET Desktop Runtime (5 or newer), or use the standalone build (section 4) |
| Status stays at "Translation service not running" | Start the API (`TranslatorService\start_cluster.bat`), check the address (`--url`), and check that the firewall allows the port |
| Translate button disabled | Not connected yet; see the status bar |
| Rows stay unchanged with "not supported" | The detected language (e.g. German) is not one the model translates (only en, es, fr) |
