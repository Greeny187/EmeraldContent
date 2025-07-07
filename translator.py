import logging
import html
import requests
from database import get_cached_translation, set_cached_translation

# Versuche, den offiziellen Google Cloud Translate-Client zu importieren
try:
    from google.cloud import translate_v2 as cloud_translate
    _translate_client = cloud_translate.Client()
    _use_cloud = True
    logging.info("Google Cloud Translate: offizieller Client geladen.")
except ImportError:
    _use_cloud = False
    logging.warning("google-cloud-translate nicht installiert, wechsle auf HTTP-Fallback.")


def translate_hybrid(source_text: str, target_lang: str) -> str:
    """
    Übersetzt einen Text in die Zielsprache `target_lang`.
    1) Prüft den Cache (translations_cache).
    2) Falls nicht im Cache, versucht Google Cloud Translate (falls verfügbar).
    3) Fällt sonst auf HTTP-API von translate.googleapis.com zurück.
    4) Speichert Ergebnis im Cache.
    5) Gibt bei Fehlern den Originaltext zurück.
    """
    # Cache-Abfrage
    try:
        cached = get_cached_translation(source_text, target_lang)
        if cached:
            return cached
    except Exception as e:
        logging.warning(f"Cache-Abfrage fehlgeschlagen: {e}")

    translated = None
    # Offizielle Cloud-API
    if _use_cloud:
        try:
            result = _translate_client.translate(
                source_text,
                target_language=target_lang,
                format_="text"
            )
            translated = result.get('translatedText')
        except Exception as e:
            logging.error(f"Cloud-Übersetzung fehlgeschlagen ({source_text}→{target_lang}): {e}")
    # HTTP-Fallback
    if not translated:
        try:
            resp = requests.get(
                'https://translate.googleapis.com/translate_a/single',
                params={
                    'client': 'gtx',
                    'sl': 'auto',
                    'tl': target_lang,
                    'dt': 't',
                    'q': source_text
                },
                timeout=5
            )
            data = resp.json()
            # Roh-HTML-Entities dekodieren
            translated = html.unescape(''.join(seg[0] for seg in data[0]))
        except Exception as e:
            logging.error(f"HTTP-Übersetzung fehlgeschlagen ({source_text}→{target_lang}): {e}")

    if not translated:
        return source_text

    # Ergebnis im Cache speichern (ohne Overrides zu überschreiben)
    try:
        set_cached_translation(source_text, target_lang, translated, override=False)
    except Exception as e:
        logging.warning(f"Cache-Speicherung fehlgeschlagen: {e}")

    return translated