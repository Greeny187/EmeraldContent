# payments.py
import os, uuid, time, hmac, hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from database import (
    ensure_payments_schema, create_payment_order, mark_payment_paid,
    set_pro_until, get_subscription_info
)

WEBSITE  = "https://greeny187.github.io/GreenyManagementBots/"
SUPPORT  = "https://t.me/+DkUfIvjyej8zNGVi"
TON_WALLET = "UQBopac1WFJGC_K48T8T8..."  # aus env oder Konstante

PROVIDERS = {
    "paypal":  {"label":"PayPal", "link_base": os.getenv("PAYPAL_LINK_BASE")},       # z.B. PayPal.Me-Link
    "coinbase":{"label":"Coinbase Pay", "link_base": os.getenv("COINBASE_LINK_BASE")},# Commerce/Pay Hosted URL
    "binance": {"label":"Binance Pay", "link_base": os.getenv("BINANCE_LINK_BASE")},
    "bybit":   {"label":"Bybit Pay",   "link_base": os.getenv("BYBIT_LINK_BASE")},
    "revolut": {"label":"Revolut Pay", "link_base": os.getenv("REVOLUT_LINK_BASE")},
    "stars":   {"label":"Telegram Stars", "link_base": os.getenv("STARS_DEEPLINK")},
}

PLANS = {
    "pro_monthly": {"label":"Pro (1 Monat)", "months":1, "price_eur": os.getenv("PRO_PRICE_EUR","4.99")},
    "pro_yearly":  {"label":"Pro (12 Monate)", "months":12, "price_eur": os.getenv("PRO_YEAR_EUR","49.00")},
}

def _build_link(link_base:str, order_id:str, price:str)->str:
    # generischer Fallback: base?ref=ORDER&amount=PRICE
    sep = "&" if "?" in (link_base or "") else "?"
    return f"{link_base}{sep}ref={order_id}&amount={price}"

def create_checkout(chat_id:int, provider:str, plan_key:str, user_id:int):
    ensure_payments_schema()
    plan = PLANS[plan_key]
    order_id = str(uuid.uuid4())
    price = str(plan["price_eur"])
    months = int(plan["months"])

    # Payment-Link bauen (Provider-agnostischer Fallback)
    pb = PROVIDERS.get(provider, {})
    link_base = pb.get("link_base")
    if provider == "stars":
        # DeepLink ins Bot-Chat, z.B. t.me/<bot>?start=buypro_<order_id>
        link = link_base or "https://t.me/your_bot_username?start=buypro_"+order_id
    elif link_base:
        link = _build_link(link_base, order_id, price)
    else:
        # Minimaler Fallback: Info-Seite
        link = WEBSITE

    create_payment_order(order_id, chat_id, provider, plan_key, price, months, user_id)
    return {"url": link, "order_id": order_id, "price": price, "months": months}

def build_pro_menu(chat_id:int):
    sub = get_subscription_info(chat_id)
    kb = []
    for key, plan in PLANS.items():
        row = []
        for prov, meta in PROVIDERS.items():
            if not meta.get("link_base") and prov != "stars":
                continue
            row.append(InlineKeyboardButton(f"{plan['label']} • {meta['label']}",
                                            callback_data=f"pay:{prov}:{key}"))
        kb.extend([row])
    kb.append([InlineKeyboardButton("🔎 Status prüfen", callback_data="pay:status")])
    return InlineKeyboardMarkup(kb)

# Webhook-Stubs (validierung nachrüstbar)
def handle_webhook(provider:str, data:dict):
    # bei erfolgreicher Zahlung: mark_payment_paid(order_id) und Laufzeit buchen
    order_id = data.get("order_id") or data.get("ref")
    if not order_id:
        return False
    ok, chat_id, months = mark_payment_paid(order_id, provider)
    if ok:
        until = datetime.now(ZoneInfo("UTC")) + timedelta(days=30*months)
        set_pro_until(chat_id, until, tier="pro")
    return ok
