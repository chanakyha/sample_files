using System.Collections.Generic;
using System.Linq;

namespace TranslatorDesktop
{
    public sealed record Language(string Code, string Name, string NativeName);

    /// <summary>The 24 official EU languages supported by TranslatorService.</summary>
    public static class Languages
    {
        public static readonly IReadOnlyList<Language> All = new List<Language>
        {
            new("bg", "Bulgarian", "Български"),
            new("hr", "Croatian", "Hrvatski"),
            new("cs", "Czech", "Čeština"),
            new("da", "Danish", "Dansk"),
            new("nl", "Dutch", "Nederlands"),
            new("en", "English", "English"),
            new("et", "Estonian", "Eesti"),
            new("fi", "Finnish", "Suomi"),
            new("fr", "French", "Français"),
            new("de", "German", "Deutsch"),
            new("el", "Greek", "Ελληνικά"),
            new("hu", "Hungarian", "Magyar"),
            new("ga", "Irish", "Gaeilge"),
            new("it", "Italian", "Italiano"),
            new("lv", "Latvian", "Latviešu"),
            new("lt", "Lithuanian", "Lietuvių"),
            new("mt", "Maltese", "Malti"),
            new("pl", "Polish", "Polski"),
            new("pt", "Portuguese", "Português"),
            new("ro", "Romanian", "Română"),
            new("sk", "Slovak", "Slovenčina"),
            new("sl", "Slovenian", "Slovenščina"),
            new("es", "Spanish", "Español"),
            new("sv", "Swedish", "Svenska"),
        };

        public static Language Get(string code) => All.First(l => l.Code == code);

        public static string NameOf(string? code) =>
            All.FirstOrDefault(l => l.Code == code)?.Name ?? code ?? "";
    }
}
