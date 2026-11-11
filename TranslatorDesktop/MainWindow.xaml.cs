using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;

namespace TranslatorDesktop;

public partial class MainWindow : Window
{
    private const int Port = 8000;
    private static readonly TimeSpan StartupTimeout = TimeSpan.FromMinutes(3);

    private static readonly string[] SampleTexts =
    [
        "The car needs a new battery.",
        "Bitte überprüfen Sie den Reifendruck vor der langen Fahrt.",
        "La voiture sera prête demain matin.",
        "El motor hace un ruido extraño al arrancar.",
        "Il tagliando è previsto per la prossima settimana.",
        "Our dealership is closed on Monday for maintenance.",
    ];

    private readonly ObservableCollection<TextRow> _rows = [];
    private readonly TranslatorApi _api;
    private readonly ServiceHost _host = new();
    private readonly CancellationTokenSource _closing = new();
    private readonly Dictionary<string, MenuItem> _languageItems = [];

    private Language _selected = Languages.Get("en");
    private bool _showingTranslation;
    private bool _serviceReady;
    private bool _busy;

    public MainWindow()
    {
        InitializeComponent();
        var url = Environment.GetEnvironmentVariable("TRANSLATOR_URL") ?? $"http://127.0.0.1:{Port}";
        _api = new TranslatorApi(new Uri(url));

        foreach (var text in SampleTexts) _rows.Add(new TextRow(text));
        RowsList.ItemsSource = _rows;
        _rows.CollectionChanged += (_, _) => UpdateHeader();

        BuildTranslateMenu();
        UpdateLanguageUi();
        UpdateHeader();
        UpdateEnabled();
    }

    // ---------- Nav bar ----------

    private void BuildTranslateMenu()
    {
        var original = new MenuItem { Header = "Show original text", Foreground = Brushes.Black };
        original.Click += (_, _) => ShowOriginal();
        TranslateMenu.Items.Add(original);
        TranslateMenu.Items.Add(new Separator());

        foreach (var lang in Languages.All)
        {
            var item = new MenuItem
            {
                Header = lang.Name,
                InputGestureText = lang.NativeName,
                Tag = lang,
                Foreground = Brushes.Black,
                FontWeight = FontWeights.Normal,
            };
            System.Windows.Automation.AutomationProperties.SetAutomationId(item, $"lang_{lang.Code}");
            item.Click += async (_, _) => await SelectLanguageAndTranslateAsync(lang);
            _languageItems[lang.Code] = item;
            TranslateMenu.Items.Add(item);
        }
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

    private void InputBox_TextChanged(object sender, TextChangedEventArgs e) =>
        Placeholder.Visibility = InputBox.Text.Length == 0 ? Visibility.Visible : Visibility.Collapsed;

    private void RemoveRow_Click(object sender, RoutedEventArgs e)
    {
        if (sender is FrameworkElement { Tag: TextRow row }) _rows.Remove(row);
    }

    private void ClearButton_Click(object sender, RoutedEventArgs e) => _rows.Clear();

    private void ShowOriginalButton_Click(object sender, RoutedEventArgs e) => ShowOriginal();

    private async Task AddRowFromInputAsync()
    {
        var text = InputBox.Text.Trim();
        if (text.Length == 0) return;
        var row = new TextRow(text);
        _rows.Add(row);
        InputBox.Clear();
        InputBox.Focus();
        RowsScroller.ScrollToEnd();
        // While the window shows translations, translate new rows straight away.
        if (_showingTranslation) await TranslateRowsAsync([row]);
    }

    // ---------- Translation ----------

    private async Task TranslateRowsAsync(IReadOnlyList<TextRow> rows)
    {
        if (rows.Count == 0 || _busy) return;
        if (!_serviceReady)
        {
            SetStatus("Translation service is still starting, please wait…", Colors.Orange);
            return;
        }

        var target = _selected;
        _busy = true;
        UpdateEnabled();
        SetStatus($"Translating {rows.Count} text(s) to {target.Name}…", Colors.Orange);
        var sw = Stopwatch.StartNew();
        try
        {
            var result = await _api.TranslateAsync(
                rows.Select(r => r.Original).ToList(), target.Code, _closing.Token);
            var texts = result.Translation[0];
            var langs = result.DetectedLang[0];
            for (var i = 0; i < rows.Count; i++)
                rows[i].ApplyTranslation(texts[i], langs[i], target.Code);
            if (rows.Count == _rows.Count) _showingTranslation = true;
            TimingText.Text = $"{rows.Count} text(s) → {target.Name} in {sw.Elapsed.TotalSeconds:0.00} s";
            SetReadyStatus();
        }
        catch (OperationCanceledException) when (_closing.IsCancellationRequested) { }
        catch (Exception ex)
        {
            SetStatus("Translation failed", Colors.Firebrick);
            MessageBox.Show(this, ex.Message, "Translation failed", MessageBoxButton.OK, MessageBoxImage.Error);
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

    // ---------- Service startup ----------

    private async void Window_Loaded(object sender, RoutedEventArgs e)
    {
        try
        {
            await ConnectToServiceAsync(_closing.Token);
        }
        catch (OperationCanceledException) { }
    }

    private async Task ConnectToServiceAsync(CancellationToken ct)
    {
        SetStatus("Connecting to translation service…", Colors.Orange);
        var health = await _api.GetHealthAsync(ct);

        if (health is null)
        {
            var dir = ServiceHost.FindServiceDir();
            if (dir is null)
            {
                SetStatus("TranslatorService folder not found (set TRANSLATOR_SERVICE_DIR)", Colors.Firebrick);
                return;
            }
            try
            {
                _host.Start(dir, Port);
            }
            catch (Exception ex)
            {
                SetStatus("Could not start translation service", Colors.Firebrick);
                MessageBox.Show(this, ex.Message, "Translation service", MessageBoxButton.OK, MessageBoxImage.Error);
                return;
            }
            SetStatus("Starting offline translation service (loading model)…", Colors.Orange);

            var sw = Stopwatch.StartNew();
            while (health is null)
            {
                await Task.Delay(500, ct);
                if (_host.HasExited)
                {
                    SetStatus("Translation service stopped unexpectedly", Colors.Firebrick);
                    MessageBox.Show(this, _host.RecentLog, "Translation service error",
                        MessageBoxButton.OK, MessageBoxImage.Error);
                    return;
                }
                if (sw.Elapsed > StartupTimeout)
                {
                    SetStatus("Translation service did not start in time", Colors.Firebrick);
                    return;
                }
                health = await _api.GetHealthAsync(ct);
            }
        }

        if (health.Status != "ok")
        {
            SetStatus("Translation model missing: run download_models.py in TranslatorService", Colors.Firebrick);
            return;
        }

        _serviceReady = true;
        _health = health;
        SetReadyStatus();
        UpdateEnabled();
    }

    private HealthInfo? _health;

    private void SetReadyStatus()
    {
        var info = _health is null ? "" : $" · {_health.Device.ToUpperInvariant()} · {_health.Workers} workers";
        SetStatus($"Ready · offline{info}{(_host.StartedByUs ? "" : " · external service")}", Colors.SeaGreen);
    }

    // ---------- UI helpers ----------

    private void SetStatus(string text, Color color)
    {
        StatusText.Text = text;
        StatusDot.Fill = new SolidColorBrush(color);
    }

    private void UpdateLanguageUi()
    {
        foreach (var (code, item) in _languageItems)
        {
            item.IsChecked = code == _selected.Code;
            item.FontWeight = code == _selected.Code ? FontWeights.SemiBold : FontWeights.Normal;
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
        _host.Dispose();
        _api.Dispose();
    }
}
