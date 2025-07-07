import logging
from google.cloud import translate_v2 as cloud_translate
from database import get_cached_translation, set_cached_translation

# Google Cloud Translate-Client initialisieren
_translate_client = cloud_translate.Client()


def translate_hybrid(source_text: str, target_lang: str) -> str:
    """
    Übersetzt einen Text in die Zielsprache `target_lang`.
    1) Prüft den Cache (translations_cache).
    2) Falls nicht im Cache, ruft Google Cloud Translate auf und speichert das Ergebnis im Cache.
    3) Gibt bei Fehlern den Originaltext zurück.
    """
    # Cache-Abfrage
    try:
        cached = get_cached_translation(source_text, target_lang)
        if cached:
            return cached
    except Exception as e:
        logging.warning(f"Cache-Abfrage fehlgeschlagen: {e}")

    # Google Cloud Translate
    try:
        result = _translate_client.translate(
            source_text,
            target_language=target_lang,
            format_="text"
        )
        translated = result.get('translatedText')
        # Ergebnis im Cache speichern (ohne bestehende Overrides zu überschreiben)
        try:
            set_cached_translation(source_text, target_lang, translated, override=False)
        except Exception as e:
            logging.warning(f"Cache-Speicherung fehlgeschlagen: {e}")
        return translated
    except Exception as e:
        logging.error(f"Übersetzung fehlgeschlagen ({source_text}→{target_lang}): {e}")
        return source_text