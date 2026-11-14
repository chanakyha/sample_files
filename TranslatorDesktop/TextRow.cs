using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace TranslatorDesktop
{
    /// <summary>One row of text in the window: the original and its current translation.</summary>
    public sealed class TextRow : INotifyPropertyChanged
    {
        private string _display;
        private string? _detectedLang;
        private string? _targetLang;
        private bool _unsupported;

        public TextRow(string original)
        {
            Original = original;
            _display = original;
        }

        public event PropertyChangedEventHandler? PropertyChanged;

        public string Original { get; }

        public string Display
        {
            get => _display;
            private set { _display = value; OnChanged(); }
        }

        public bool IsTranslated => _targetLang is not null;

        /// <summary>Shown when hovering over the row: which language the text was translated
        /// from and to, and the original text. Null (no tooltip) before translation.</summary>
        public string? ToolTipText
        {
            get
            {
                if (!IsTranslated) return null;
                var from = Languages.NameOf(_detectedLang);
                var to = Languages.NameOf(_targetLang);
                if (_detectedLang == "und")
                    return $"No language detected (no letters); not translated\nOriginal: {Original}";
                if (_unsupported)
                    return $"Detected {from}, which cannot be translated; shown unchanged\nOriginal: {Original}";
                if (_detectedLang == _targetLang)
                    return $"Already in {to}; not translated\nOriginal: {Original}";
                return $"Translated from {from} to {to}\nOriginal: {Original}";
            }
        }

        public void ApplyTranslation(string text, string detectedLang, string targetLang, bool unsupported)
        {
            _detectedLang = detectedLang;
            _targetLang = targetLang;
            _unsupported = unsupported;
            Display = text;
            RaiseState();
        }

        public void ResetToOriginal()
        {
            _detectedLang = null;
            _targetLang = null;
            _unsupported = false;
            Display = Original;
            RaiseState();
        }

        private void RaiseState()
        {
            OnChanged(nameof(IsTranslated));
            OnChanged(nameof(ToolTipText));
        }

        private void OnChanged([CallerMemberName] string? name = null) =>
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
    }
}
