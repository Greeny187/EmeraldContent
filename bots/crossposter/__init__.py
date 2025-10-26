"""
Crossposter Bot-Paket.

ENV-Konvention:
  BOT2_TOKEN  -> Crossposter-Bot-Token (wird von bot.py gelesen)
  BOT2_KEY    -> "crossposter" (empfohlen), ergibt Route /webhook/crossposter

Dieses Paket stellt zwei Module bereit:
  - app.py      -> registriert die PTB-Handler (MessageHandler, /crossposter)
  - miniapp.py  -> bindet die MiniApp-API unter /miniapi an den AIOHTTP-Server
"""
__all__ = ["app", "miniapp"]
