using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace TranslatorDesktop
{
    /// <summary>One text in the list, with its detected language.</summary>
    public sealed class TextEntry : INotifyPropertyChanged
    {
        private string _language = "detecting...";

        public TextEntry(string text) => Text = text;

        public string Text { get; }
        public string? LangCode { get; set; }

        public string Language
        {
            get => _language;
            set
            {
                _language = value;
                PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(nameof(Display)));
            }
        }

        public string Display => $"{Text}    [{_language}]";

        public override string ToString() => Display; // also the name screen readers / UI tests see

        public event PropertyChangedEventHandler? PropertyChanged;
    }

    /// <summary>
    /// Plain window: add texts at the bottom (their language is detected), translate them all
    /// with the English / French / Spanish buttons, read the result in the box at the bottom.
    ///
    /// The translation API is a separate program. If it is not already running at the API
    /// address, the app starts TranslatorServer.exe as a hidden background process
    /// (see <see cref="ServerProcess"/>) and stops it again when the window closes.
    /// </summary>
    public partial class MainWindow : Window
    {
        private readonly ObservableCollection<TextEntry> _entries = new();
        private readonly TranslatorApi _api;
        private readonly ServerProcess _server;
        private readonly CancellationTokenSource _closing = new();
        private bool _ready;
        private bool _busy;

        public MainWindow()
        {
            InitializeComponent();
            _api = new TranslatorApi(new Uri(ResolveApiUrl()));
            _server = new ServerProcess(_api.BaseAddress);
            TextList.ItemsSource = _entries;
            UpdateButtons();
            Loaded += async (_, _) =>
            {
                InputBox.Focus();
                await ConnectAsync(_closing.Token);
            };
            Closed += (_, _) =>
            {
                _closing.Cancel();
                _server.Stop();
                _api.Dispose();
            };
        }

        /// <summary>API address: --url http://host:port on the command line, else the
        /// TRANSLATOR_URL environment variable, else http://127.0.0.1:8000.</summary>
        private static string ResolveApiUrl()
        {
            var args = Environment.GetCommandLineArgs();
            for (var i = 1; i < args.Length - 1; i++)
                if (args[i] == "--url") return args[i + 1];
            var fromEnv = Environment.GetEnvironmentVariable("TRANSLATOR_URL");
            return string.IsNullOrWhiteSpace(fromEnv) ? "http://127.0.0.1:8000" : fromEnv;
        }

        // ------------------------------------------------------------------ server connection

        private async Task ConnectAsync(CancellationToken ct)
        {
            try
            {
                var health = await _api.GetHealthAsync(ct);
                if (health == null)
                {
                    var started = _server.TryStart(out var message);
                    SetStatus(message);
                    if (!started) { await MonitorAsync(ct); return; }
                    // The models take a few seconds to load
                    var deadline = DateTime.UtcNow.AddMinutes(3);
                    while (health == null && DateTime.UtcNow < deadline && !_server.HasExited)
                    {
                        await Task.Delay(1000, ct);
                        health = await _api.GetHealthAsync(ct);
                    }
                    if (health == null)
                    {
                        SetStatus(_server.HasExited
                            ? $"The translation server stopped (exit code {_server.ExitCode}). See its logs folder: {_server.LogFolder}"
                            : $"The translation server did not start within 3 minutes ({_api.BaseAddress}).");
                        await MonitorAsync(ct);
                        return;
                    }
                }
                OnHealth(health);
                await MonitorAsync(ct);
            }
            catch (OperationCanceledException) { }
        }

        /// <summary>Checks the API every few seconds and enables the buttons while it answers.</summary>
        private async Task MonitorAsync(CancellationToken ct)
        {
            while (!ct.IsCancellationRequested)
            {
                await Task.Delay(_ready ? 5000 : 2000, ct);
                var health = await _api.GetHealthAsync(ct);
                if (health == null)
                {
                    if (_ready || !StatusText.Text.StartsWith("The translation server"))
                        SetStatus($"Translation server not reachable at {_api.BaseAddress} (retrying...)");
                    _ready = false;
                    UpdateButtons();
                }
                else if (!_ready || health.Status != "ok")
                {
                    OnHealth(health);
                }
            }
        }

        private void OnHealth(HealthInfo health)
        {
            _ready = health.Status == "ok";
            SetStatus(_ready
                ? $"Ready - {_api.BaseAddress}" + (_server.StartedByApp ? " (server started by this app)" : "")
                : "The translation server is running but its models are missing.");
            UpdateButtons();
            _ = DetectPendingAsync();
        }

        // ------------------------------------------------------------------ adding texts

        private void InputBox_KeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key == Key.Enter)
            {
                AddText();
                e.Handled = true;
            }
        }

        private void AddButton_Click(object sender, RoutedEventArgs e) => AddText();

        private void AddText()
        {
            var text = InputBox.Text.Trim();
            if (text.Length == 0) return;
            var entry = new TextEntry(text);
            _entries.Add(entry);
            TextList.ScrollIntoView(entry);
            InputBox.Clear();
            InputBox.Focus();
            UpdateButtons();
            if (_ready) _ = DetectAsync(new[] { entry });
            else entry.Language = "waiting for the server...";
        }

        private Task DetectPendingAsync() =>
            DetectAsync(_entries.Where(e => e.LangCode == null).ToArray());

        private async Task DetectAsync(TextEntry[] entries)
        {
            if (entries.Length == 0) return;
            foreach (var e in entries) e.Language = "detecting...";
            try
            {
                var result = await _api.DetectAsync(entries.Select(e => e.Text).ToList(), _closing.Token);
                for (var i = 0; i < entries.Length; i++)
                {
                    entries[i].LangCode = result.DetectedLang[i];
                    entries[i].Language = result.DetectedLang[i] == "und"
                        ? "no language (no letters)"
                        : result.Language[i];
                }
            }
            catch (TranslatorApiException ex)
            {
                foreach (var e in entries) e.Language = "not detected";
                SetStatus(ex.Message);
            }
            catch (OperationCanceledException) { }
        }

        // ------------------------------------------------------------------ translating

        private async void TranslateButton_Click(object sender, RoutedEventArgs e)
        {
            var target = (string)((Button)sender).Tag;
            var targetName = Languages.NameOf(target);
            if (_entries.Count == 0)
            {
                ResultBox.Text = "Add a text first (type it in the box above and press Enter).";
                return;
            }

            _busy = true;
            UpdateButtons();
            SetStatus($"Translating {_entries.Count} text(s) into {targetName}...");
            try
            {
                var entries = _entries.ToArray();
                var response = await _api.TranslateAsync(entries.Select(x => x.Text).ToList(), target, _closing.Token);
                var sb = new StringBuilder();
                sb.AppendLine($"Translation into {targetName}:");
                for (var i = 0; i < entries.Length; i++)
                {
                    var source = response.DetectedLang[0][i];
                    var translated = response.Translated?[0][i] ?? true;
                    var translation = response.Translation[0][i];
                    entries[i].LangCode = source;
                    if (source != "und") entries[i].Language = Languages.NameOf(source);

                    sb.AppendLine();
                    sb.AppendLine(entries[i].Text);
                    if (translated)
                        sb.AppendLine($"{Languages.NameOf(source)} -> {targetName}: {translation}");
                    else if (source == target)
                        sb.AppendLine($"Already {targetName}, unchanged.");
                    else if (source == "und")
                        sb.AppendLine("No language detected (no letters), unchanged.");
                    else
                        sb.AppendLine($"{Languages.NameOf(source)} cannot be translated (only English, French and Spanish), unchanged.");
                }
                ResultBox.Text = sb.ToString();
                ResultBox.ScrollToHome();
                SetStatus($"Ready - {_api.BaseAddress}");
            }
            catch (TranslatorApiException ex)
            {
                ResultBox.Text = ex.Message;
                SetStatus(ex.Message);
            }
            catch (OperationCanceledException) { }
            finally
            {
                _busy = false;
                UpdateButtons();
            }
        }

        // ------------------------------------------------------------------ helpers

        private void UpdateButtons()
        {
            var enabled = _ready && !_busy;
            EnglishButton.IsEnabled = enabled;
            FrenchButton.IsEnabled = enabled;
            SpanishButton.IsEnabled = enabled;
        }

        private void SetStatus(string text) => StatusText.Text = text;
    }
}
