# EU Translator

Offline translation between the 24 official EU languages with automatic language detection.

| Folder | What it is |
|--------|------------|
| [TranslatorService/](TranslatorService/) | Python REST API (FastAPI + NLLB-200 via CTranslate2). See its [README](TranslatorService/README.md). |
| [TranslatorDesktop/](TranslatorDesktop/) | Windows desktop app (.NET 10 WPF) that uses the API. |

## Desktop app

- Rows of text in any EU language; add more with the text box at the bottom (Enter or **Add**).
- **Translate** in the top bar: hover to see the 24 languages; click one to translate every row
  into it. The chosen language is ticked and becomes the target of the **Translate →** button.
- Each row shows the translation, the original underneath, and a badge with the detected
  source → target language. **Show original** switches back.
- While a translation is shown, newly added rows are translated immediately.

The app starts the Python service automatically in the background (hidden) and stops it when the
window closes. If a service is already running on port 8000, it is reused.

### Run

Set up `TranslatorService` first (virtual environment + model, see its README), then:

```powershell
dotnet run --project TranslatorDesktop
```

or open `Translator.slnx` in Visual Studio and press F5.

### Build a standalone .exe

```powershell
dotnet publish TranslatorDesktop -c Release -r win-x64 --self-contained -p:PublishSingleFile=true -o publish
```

Put `publish\TranslatorDesktop.exe` anywhere inside (or below) the `Translator` folder so it can
find `TranslatorService`, or set `TRANSLATOR_SERVICE_DIR` to the service folder.

| Variable | Default | Meaning |
|----------|---------|---------|
| `TRANSLATOR_SERVICE_DIR` | auto | Path to the `TranslatorService` folder |
| `TRANSLATOR_URL` | `http://127.0.0.1:8000` | Use an already-running service elsewhere |
