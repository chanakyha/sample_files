"""The 24 official EU languages: ISO 639-1 code -> (English name, NLLB-200 code)."""

LANGUAGES: dict[str, tuple[str, str]] = {
    "bg": ("Bulgarian", "bul_Cyrl"),
    "cs": ("Czech", "ces_Latn"),
    "da": ("Danish", "dan_Latn"),
    "de": ("German", "deu_Latn"),
    "el": ("Greek", "ell_Grek"),
    "en": ("English", "eng_Latn"),
    "es": ("Spanish", "spa_Latn"),
    "et": ("Estonian", "est_Latn"),
    "fi": ("Finnish", "fin_Latn"),
    "fr": ("French", "fra_Latn"),
    "ga": ("Irish", "gle_Latn"),
    "hr": ("Croatian", "hrv_Latn"),
    "hu": ("Hungarian", "hun_Latn"),
    "it": ("Italian", "ita_Latn"),
    "lt": ("Lithuanian", "lit_Latn"),
    "lv": ("Latvian", "lvs_Latn"),
    "mt": ("Maltese", "mlt_Latn"),
    "nl": ("Dutch", "nld_Latn"),
    "pl": ("Polish", "pol_Latn"),
    "pt": ("Portuguese", "por_Latn"),
    "ro": ("Romanian", "ron_Latn"),
    "sk": ("Slovak", "slk_Latn"),
    "sl": ("Slovenian", "slv_Latn"),
    "sv": ("Swedish", "swe_Latn"),
}

LANGUAGE_CODES: list[str] = list(LANGUAGES)
