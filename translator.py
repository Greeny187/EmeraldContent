from database import get_cached_translation, set_cached_translation
from googletrans import Translator  # oder DeepL-Client deiner Wahl

translator = Translator()

DEFAULT_LANG = 'de'

def t(source_text: str, lang: str = DEFAULT_LANG) -> str:
    """
    Übersetzt source_text in die Zielsprache lang.
    Rückt den Original-Text zurück, wenn lang == DEFAULT_LANG.
    Nutzt Cache + Übersetzungs-API.
    """
    # 1. Wenn Deutsch (Standard), Original zurückgeben
    if lang == DEFAULT_LANG:
        return source_text

    # 2. Aus Cache holen
    cached = get_cached_translation(source_text, lang)
    if cached:
        return cached

    # 3. Übersetzen
    try:
        result = translator.translate(source_text, dest=lang).text
    except Exception:
        # Bei Fehlschlag immer noch Original-Text zurückgeben
        return source_text

    # 4. In Cache speichern
    set_cached_translation(source_text, lang, result, override=False)
    return result