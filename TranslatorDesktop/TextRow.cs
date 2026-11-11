using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace TranslatorDesktop;

/// <summary>One row of text in the window: the original and its current translation.</summary>
public sealed class TextRow(string original) : INotifyPropertyChanged
{
    private string _display = original;
    private string? _detectedLang;
    private string? _targetLang;

    public event PropertyChangedEventHandler? PropertyChanged;

    public string Original { get; } = original;

    public string Display
    {
        get => _display;
        private set { _display = value; OnChanged(); }
    }

    public bool IsTranslated => _targetLang is not null;

    /// <summary>The original is shown underneath when the text actually changed.</summary>
    public bool ShowOriginal => IsTranslated && Display != Original;

    public string Badge => IsTranslated
        ? $"{(_detectedLang ?? "?").ToUpperInvariant()} → {_targetLang!.ToUpperInvariant()}"
        : "";

    public string BadgeToolTip => IsTranslated
        ? $"Detected {Languages.NameOf(_detectedLang)}, shown in {Languages.NameOf(_targetLang)}"
        : "";

    public void ApplyTranslation(string text, string detectedLang, string targetLang)
    {
        _detectedLang = detectedLang;
        _targetLang = targetLang;
        Display = text;
        RaiseState();
    }

    public void ResetToOriginal()
    {
        _detectedLang = null;
        _targetLang = null;
        Display = Original;
        RaiseState();
    }

    private void RaiseState()
    {
        OnChanged(nameof(IsTranslated));
        OnChanged(nameof(ShowOriginal));
        OnChanged(nameof(Badge));
        OnChanged(nameof(BadgeToolTip));
    }

    private void OnChanged([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}
