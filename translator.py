import logging
from googletrans import Translator as GoogleTranslator
from database import get_cached_translation, set_cached_translation

# Google Translator-Client initialisieren
_google_translator = GoogleTranslator()


def translate_hybrid(source_text: str, target_lang: str) -> str:
    """
    Übersetzt einen Text in die Zielsprache `target_lang`.
    1) Prüft den Cache (translations_cache).
    2) Falls nicht im Cache, ruft Google Translate auf und speichert Ergebnis im Cache.
    3) Gibt bei Fehlern den Originaltext zurück.
    """
    # Cache-Abfrage
    try:
        cached = get_cached_translation(source_text, target_lang)
        if cached:
            return cached
    except Exception as e:
        logging.warning(f"Cache-Abfrage fehlgeschlagen: {e}")
    
    # Google Translate
    try:
        res = _google_translator.translate(source_text, dest=target_lang)
        translated = res.text
        # Ergebnis speichern (nicht überschreiben existierender Overrides)
        try:
            set_cached_translation(source_text, target_lang, translated, override=False)
        except Exception as e:
            logging.warning(f"Cache-Speicherung fehlgeschlagen: {e}")
        return translated
    except Exception as e:
        logging.error(f"Übersetzung fehlgeschlagen ({source_text}→{target_lang}): {e}")
        return source_text
