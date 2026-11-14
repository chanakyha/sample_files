using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace TranslatorDesktop
{
    /// <summary>
    /// The desktop client. It does not start the translation service: the Python API runs as a
    /// separate process (see TranslatorService\start_api.bat) and the app connects to it over
    /// HTTP, reconnecting automatically whenever the service is started or restarted.
    /// </summary>
    public partial class MainWindow : Window
    {
        private const string DefaultUrl = "http://127.0.0.1:8000";
        private static readonly TimeSpan PollWhileDown = TimeSpan.FromSeconds(2);
        private static readonly TimeSpan PollWhileUp = TimeSpan.FromSeconds(5);

        private static readonly string[] SampleTexts =
        {
            "The car needs a new battery.",
            "La voiture sera prête demain matin.",
            "El motor hace un ruido extraño al arrancar.",
            "Our dealership is closed on Monday for maintenance.",
            "¿Dónde está la estación de tren?",
            "Je voudrais réserver une table pour deux personnes.",
        };

        // Until the service reports its languages (GET /languages)
        private static readonly string[] DefaultTranslationLanguages = { "en", "es", "fr" };
        private HashSet<string> _supported = new(DefaultTranslationLanguages);

        private readonly ObservableCollection<TextRow> _rows = new();
        private readonly TranslatorApi _api;
        private readonly CancellationTokenSource _closing = new();
        private readonly Dictionary<string, MenuItem> _languageItems = new();

        private Language _selected = Languages.Get("en");
        private HealthInfo? _health;
        private bool _showingTranslation;
        private bool _serviceReady;
        private bool _busy;

        public MainWindow()
        {
            InitializeComponent();
            _api = new TranslatorApi(new Uri(ServiceUrl()));

            foreach (var text in SampleTexts) _rows.Add(new TextRow(text));
            RowsList.ItemsSource = _rows;
            _rows.CollectionChanged += (_, _) => UpdateHeader();

            BuildTranslateMenu(DefaultTranslationLanguages);
            UpdateLanguageUi();
            UpdateHeader();
            UpdateEnabled();
        }

        /// <summary>API address: --url http://host:port on the command line, else the
        /// TRANSLATOR_URL environment variable, else http://127.0.0.1:8000.</summary>
        private static string ServiceUrl()
        {
            var args = Environment.GetCommandLineArgs();
            for (var i = 1; i < args.Length - 1; i++)
                if (args[i] == "--url") return args[i + 1];
            var fromEnv = Environment.GetEnvironmentVariable("TRANSLATOR_URL");
            return string.IsNullOrWhiteSpace(fromEnv) ? DefaultUrl : fromEnv;
        }

        // ---------- Nav bar ----------

        private void BuildTranslateMenu(IEnumerable<string> codes)
        {
            _supported = new HashSet<string>(codes);
            TranslateMenu.Items.Clear();
            _languageItems.Clear();
            var original = new MenuItem { Header = "Show original text" };
            original.Click += (_, _) => ShowOriginal();
            TranslateMenu.Items.Add(original);
            TranslateMenu.Items.Add(new Separator());

            foreach (var lang in Languages.All.Where(l => _supported.Contains(l.Code)))
            {
                var item = new MenuItem
                {
                    Header = lang.Name,
                    InputGestureText = lang.NativeName,
                    Tag = lang,
                };
                System.Windows.Automation.AutomationProperties.SetAutomationId(item, $"lang_{lang.Code}");
                item.Click += async (_, _) => await SelectLanguageAndTranslateAsync(lang);
                _languageItems[lang.Code] = item;
                TranslateMenu.Items.Add(item);
            }
            UpdateEnabled();
        }

        private void TranslateMenu_MouseEnter(object sender, MouseEventArgs e)
        {
            if (TranslateMenu.IsEnabled) TranslateMenu.IsSubmenuOpen = true;
        }

        private async Task SelectLanguageAndTranslateAsync(Language lang)
        {
            _selected = lang;
            UpdateLanguageUi();
            await TranslateRowsAsync(_rows.ToList());
        }

        // ---------- Buttons / input ----------

        private async void TranslateButton_Click(object sender, RoutedEventArgs e) =>
            await TranslateRowsAsync(_rows.ToList());

        private async void AddButton_Click(object sender, RoutedEventArgs e) => await AddRowFromInputAsync();

        private async void InputBox_KeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter)
            {
                e.Handled = true;
                await AddRowFromInputAsync();
            }
        }

        private void RemoveRow_Click(object sender, RoutedEventArgs e)
        {
            if (sender is FrameworkElement { Tag: TextRow row }) _rows.Remove(row);
        }

        private void ClearButton_Click(object sender, RoutedEventArgs e)
        {
            _rows.Clear();
            _showingTranslation = false;
            TimingText.Text = "";
        }

        private void ShowOriginalButton_Click(object sender, RoutedEventArgs e) => ShowOriginal();

        private async Task AddRowFromInputAsync()
        {
            var text = InputBox.Text.Trim();
            if (text.Length == 0) return;
            var row = new TextRow(text);
            _rows.Add(row);
            InputBox.Clear();
            InputBox.Focus();
            RowsList.ScrollIntoView(row);
            // While the window shows translations, translate new rows straight away.
            if (_showingTranslation) await TranslateRowsAsync(new[] { row });
        }

        // ---------- Translation ----------

        private async Task TranslateRowsAsync(IReadOnlyList<TextRow> rows)
        {
            if (rows.Count == 0 || _busy) return;
            if (!_serviceReady)
            {
                SetNotRunningStatus();
                return;
            }

            var target = _selected;
            _busy = true;
            UpdateEnabled();
            SetStatus($"Translating {rows.Count} text(s) to {target.Name}…");
            var sw = Stopwatch.StartNew();
            try
            {
                var result = await _api.TranslateAsync(
                    rows.Select(r => r.Original).ToList(), target.Code, _closing.Token);
                var texts = result.Translation[0];
                var langs = result.DetectedLang[0];
                for (var i = 0; i < rows.Count; i++)
                {
                    var unsupported = langs[i] != "und" && !_supported.Contains(langs[i]);
                    rows[i].ApplyTranslation(texts[i], langs[i], target.Code, unsupported);
                }
                if (rows.Count == _rows.Count) _showingTranslation = true;
                TimingText.Text = $"{rows.Count} text(s) → {target.Name} in {sw.Elapsed.TotalSeconds:0.00} s";
                SetReadyStatus();
            }
            catch (OperationCanceledException) when (_closing.IsCancellationRequested) { }
            catch (Exception ex)
            {
                // Service stopped in the meantime: show it in the status bar; the health
                // monitor reconnects when the service is started again.
                if (await _api.GetHealthAsync(_closing.Token) is null)
                {
                    _serviceReady = false;
                    SetNotRunningStatus();
                }
                else
                {
                    SetStatus("Translation failed");
                    MessageBox.Show(this, ex.Message, "Translation failed", MessageBoxButton.OK, MessageBoxImage.Error);
                }
            }
            finally
            {
                _busy = false;
                UpdateEnabled();
            }
        }

        private void ShowOriginal()
        {
            foreach (var row in _rows) row.ResetToOriginal();
            _showingTranslation = false;
            TimingText.Text = "";
        }

        // ---------- Connection to the separate API ----------

        private async void Window_Loaded(object sender, RoutedEventArgs e)
        {
            SetStatus($"Connecting to translation service at {_api.BaseAddress}…");
            try
            {
                await MonitorServiceAsync(_closing.Token);
            }
            catch (OperationCanceledException) { }
        }

        /// <summary>Polls GET /health for as long as the window is open and updates the UI
        /// when the service comes up, goes down or has no model.</summary>
        private async Task MonitorServiceAsync(CancellationToken ct)
        {
            while (!ct.IsCancellationRequested)
            {
                var health = await _api.GetHealthAsync(ct);
                if (health is null)
                {
                    if (_serviceReady || _health is not null || !StatusIsNotRunning())
                    {
                        _serviceReady = false;
                        _health = null;
                        if (!_busy) SetNotRunningStatus();
                        UpdateEnabled();
                    }
                }
                else if (health.Status != "ok")
                {
                    _serviceReady = false;
                    _health = health;
                    SetStatus("Translation model missing: train it with the scripts in " +
                              "TranslatorService\\training (see README)");
                    UpdateEnabled();
                }
                else if (!_serviceReady)
                {
                    await OnServiceUpAsync(health, ct);
                }
                await Task.Delay(_serviceReady ? PollWhileUp : PollWhileDown, ct);
            }
        }

        private async Task OnServiceUpAsync(HealthInfo health, CancellationToken ct)
        {
            var languages = await _api.GetLanguagesAsync(ct);
            if (languages is { Languages: { Count: > 0 } })
            {
                BuildTranslateMenu(languages.Languages.Keys);
                if (!_supported.Contains(_selected.Code))
                    _selected = Languages.Get(languages.Languages.Keys.First());
                UpdateLanguageUi();
            }
            _health = health;
            _serviceReady = true;
            SetReadyStatus();
            UpdateEnabled();
        }

        private bool StatusIsNotRunning() => StatusText.Text.StartsWith("Translation service not running");

        private void SetNotRunningStatus() =>
            SetStatus($"Translation service not running at {_api.BaseAddress} · start it with " +
                      "TranslatorService\\start_cluster.bat or start_api.bat (retrying…)");

        private void SetReadyStatus()
        {
            var info = _health is null ? "" : $" · {_health.Device.ToUpperInvariant()} · {_health.Workers} workers";
            SetStatus($"Ready · offline{info} · {_api.BaseAddress.Authority}");
        }

        // ---------- UI helpers ----------

        private void SetStatus(string text) => StatusText.Text = text;

        private void UpdateLanguageUi()
        {
            foreach (var pair in _languageItems)
            {
                pair.Value.IsChecked = pair.Key == _selected.Code;
            }
            TranslateButton.Content = $"Translate → {_selected.Name}";
            TranslateMenu.Header = $"_Translate  ·  {_selected.Name}";
            LanguageText.Text = $"Target: {_selected.Name} ({_selected.NativeName})";
        }

        private void UpdateHeader() =>
            HeaderText.Text = _rows.Count == 1 ? "1 text" : $"{_rows.Count} texts";

        private void UpdateEnabled()
        {
            var canTranslate = _serviceReady && !_busy;
            TranslateButton.IsEnabled = canTranslate;
            foreach (var item in _languageItems.Values) item.IsEnabled = canTranslate;
            Cursor = _busy ? Cursors.AppStarting : null;
        }

        private void Window_Closed(object? sender, EventArgs e)
        {
            _closing.Cancel();
            _api.Dispose();
        }
    }
}
