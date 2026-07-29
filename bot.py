import os
import logging
from datetime import datetime

import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# In-memory storage. NOTE: this resets on every redeploy/restart on Railway.
# watchlists: {chat_id: set(coin_id, ...)}
# portfolios: {chat_id: {coin_id: amount}}
watchlists: dict[int, set[str]] = {}
portfolios: dict[int, dict[str, float]] = {}

# Cache of {symbol_or_name_lowercase: coingecko_id}, built once on first use
_coin_id_cache: dict[str, str] = {}


def get_coin_id(query: str) -> str | None:
    """Resolve a user-typed symbol/name (e.g. 'btc', 'bitcoin') to a CoinGecko id."""
    query = query.lower().strip()
    if not _coin_id_cache:
        try:
            resp = requests.get(f"{COINGECKO_BASE}/coins/list", timeout=15)
            resp.raise_for_status()
            for coin in resp.json():
                _coin_id_cache.setdefault(coin["symbol"].lower(), coin["id"])
                _coin_id_cache.setdefault(coin["name"].lower(), coin["id"])
                _coin_id_cache.setdefault(coin["id"].lower(), coin["id"])
        except requests.RequestException as e:
            logger.error("Failed to load coin list: %s", e)
            return None
    return _coin_id_cache.get(query)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Welcome to *CoinMarketCap66_Bot* 📊\n\n"
        "Track live crypto prices, market caps, rankings, charts, "
        "your watchlist, and your portfolio — all from Telegram.\n\n"
        "Type /help to see everything I can do."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "*Commands*\n\n"
        "/price <coin> — current price, market cap, 24h volume & change\n"
        "/top <n> — top N coins by market cap (default 10, max 50)\n"
        "/chart <coin> <days> — high/low/avg over the period (default 7 days)\n"
        "/trending — coins trending on CoinGecko right now\n\n"
        "*Watchlist*\n"
        "/watch <coin> — add a coin to your watchlist\n"
        "/unwatch <coin> — remove a coin\n"
        "/watchlist — show your watchlist with live prices\n\n"
        "*Portfolio*\n"
        "/addholding <coin> <amount> — add/update a holding\n"
        "/removeholding <coin> — remove a holding\n"
        "/portfolio — show your holdings and total value\n\n"
        "/convert <amount> <coin> <currency> — convert crypto to a fiat currency\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /price <coin>  e.g. /price bitcoin")
        return
    coin_id = get_coin_id(context.args[0])
    if not coin_id:
        await update.message.reply_text("Couldn't find that coin. Try the full name, e.g. 'ethereum'.")
        return

    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": "usd",
                "include_market_cap": "true",
                "include_24hr_vol": "true",
                "include_24hr_change": "true",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get(coin_id)
        if not data:
            await update.message.reply_text("No data found for that coin.")
            return

        change = data.get("usd_24h_change", 0) or 0
        arrow = "🟢" if change >= 0 else "🔴"
        text = (
            f"*{coin_id.capitalize()}*\n"
            f"Price: ${data.get('usd', 0):,.4f}\n"
            f"Market Cap: ${data.get('usd_market_cap', 0):,.0f}\n"
            f"24h Volume: ${data.get('usd_24h_vol', 0):,.0f}\n"
            f"24h Change: {arrow} {change:.2f}%"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
    except requests.RequestException as e:
        logger.error("price() error: %s", e)
        await update.message.reply_text("Error fetching price data. Try again shortly.")


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    n = 10
    if context.args:
        try:
            n = max(1, min(50, int(context.args[0])))
        except ValueError:
            await update.message.reply_text("Usage: /top <number>  e.g. /top 15")
            return

    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": n,
                "page": 1,
                "sparkline": "false",
            },
            timeout=15,
        )
        resp.raise_for_status()
        coins = resp.json()
        lines = [f"*Top {n} by Market Cap*\n"]
        for i, c in enumerate(coins, start=1):
            change = c.get("price_change_percentage_24h") or 0
            arrow = "🟢" if change >= 0 else "🔴"
            lines.append(
                f"{i}. {c['symbol'].upper()} — ${c['current_price']:,.2f} "
                f"{arrow} {change:.1f}%"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except requests.RequestException as e:
        logger.error("top() error: %s", e)
        await update.message.reply_text("Error fetching rankings. Try again shortly.")


async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /chart <coin> <days>  e.g. /chart bitcoin 7")
        return
    coin_id = get_coin_id(context.args[0])
    if not coin_id:
        await update.message.reply_text("Couldn't find that coin. Try the full name, e.g. 'ethereum'.")
        return
    days = 7
    if len(context.args) > 1:
        try:
            days = max(1, min(365, int(context.args[1])))
        except ValueError:
            pass

    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/coins/{coin_id}/market_chart",
            params={"vs_currency": "usd", "days": days},
            timeout=15,
        )
        resp.raise_for_status()
        prices = [p[1] for p in resp.json().get("prices", [])]
        if not prices:
            await update.message.reply_text("No chart data available for that coin.")
            return
        high, low, current = max(prices), min(prices), prices[-1]
        avg = sum(prices) / len(prices)
        text = (
            f"*{coin_id.capitalize()} — last {days}d*\n"
            f"Current: ${current:,.4f}\n"
            f"High: ${high:,.4f}\n"
            f"Low: ${low:,.4f}\n"
            f"Average: ${avg:,.4f}"
        )
        await update.message.reply_text(text, parse_mode="Markdown")
    except requests.RequestException as e:
        logger.error("chart() error: %s", e)
        await update.message.reply_text("Error fetching chart data. Try again shortly.")


async def trending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        resp = requests.get(f"{COINGECKO_BASE}/search/trending", timeout=15)
        resp.raise_for_status()
        coins = resp.json().get("coins", [])
        if not coins:
            await update.message.reply_text("No trending data right now.")
            return
        lines = ["*🔥 Trending on CoinGecko*\n"]
        for i, c in enumerate(coins[:10], start=1):
            item = c["item"]
            lines.append(f"{i}. {item['name']} ({item['symbol'].upper()})")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except requests.RequestException as e:
        logger.error("trending() error: %s", e)
        await update.message.reply_text("Error fetching trending coins. Try again shortly.")


async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /watch <coin>  e.g. /watch bitcoin")
        return
    coin_id = get_coin_id(context.args[0])
    if not coin_id:
        await update.message.reply_text("Couldn't find that coin.")
        return
    chat_id = update.effective_chat.id
    watchlists.setdefault(chat_id, set()).add(coin_id)
    await update.message.reply_text(f"Added {coin_id} to your watchlist.")


async def unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /unwatch <coin>")
        return
    coin_id = get_coin_id(context.args[0])
    chat_id = update.effective_chat.id
    if coin_id and coin_id in watchlists.get(chat_id, set()):
        watchlists[chat_id].remove(coin_id)
        await update.message.reply_text(f"Removed {coin_id} from your watchlist.")
    else:
        await update.message.reply_text("That coin isn't on your watchlist.")


async def watchlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    coins = watchlists.get(chat_id)
    if not coins:
        await update.message.reply_text("Your watchlist is empty. Add one with /watch <coin>.")
        return

    try:
        ids = ",".join(coins)
        resp = requests.get(
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": ids, "vs_currencies": "usd", "include_24hr_change": "true"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        lines = ["*⭐ Your Watchlist*\n"]
        for coin_id in coins:
            d = data.get(coin_id, {})
            change = d.get("usd_24h_change", 0) or 0
            arrow = "🟢" if change >= 0 else "🔴"
            lines.append(f"{coin_id}: ${d.get('usd', 0):,.4f} {arrow} {change:.1f}%")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except requests.RequestException as e:
        logger.error("watchlist_cmd() error: %s", e)
        await update.message.reply_text("Error fetching watchlist prices. Try again shortly.")


async def add_holding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addholding <coin> <amount>  e.g. /addholding bitcoin 0.5")
        return
    coin_id = get_coin_id(context.args[0])
    if not coin_id:
        await update.message.reply_text("Couldn't find that coin.")
        return
    try:
        amount = float(context.args[1])
    except ValueError:
        await update.message.reply_text("Amount must be a number.")
        return

    chat_id = update.effective_chat.id
    portfolios.setdefault(chat_id, {})[coin_id] = amount
    await update.message.reply_text(f"Set holding: {amount} {coin_id}.")


async def remove_holding(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /removeholding <coin>")
        return
    coin_id = get_coin_id(context.args[0])
    chat_id = update.effective_chat.id
    if coin_id and coin_id in portfolios.get(chat_id, {}):
        del portfolios[chat_id][coin_id]
        await update.message.reply_text(f"Removed {coin_id} from your portfolio.")
    else:
        await update.message.reply_text("You don't have that holding.")


async def portfolio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    holdings = portfolios.get(chat_id)
    if not holdings:
        await update.message.reply_text("Your portfolio is empty. Add one with /addholding <coin> <amount>.")
        return

    try:
        ids = ",".join(holdings.keys())
        resp = requests.get(
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": ids, "vs_currencies": "usd"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        lines = ["*💼 Your Portfolio*\n"]
        total = 0.0
        for coin_id, amount in holdings.items():
            unit_price = data.get(coin_id, {}).get("usd", 0)
            value = unit_price * amount
            total += value
            lines.append(f"{amount} {coin_id} = ${value:,.2f}")
        lines.append(f"\n*Total: ${total:,.2f}*")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    except requests.RequestException as e:
        logger.error("portfolio_cmd() error: %s", e)
        await update.message.reply_text("Error fetching portfolio value. Try again shortly.")


async def convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /convert <amount> <coin> <currency>  e.g. /convert 2 bitcoin eur")
        return
    try:
        amount = float(context.args[0])
    except ValueError:
        await update.message.reply_text("Amount must be a number.")
        return
    coin_id = get_coin_id(context.args[1])
    currency = context.args[2].lower()
    if not coin_id:
        await update.message.reply_text("Couldn't find that coin.")
        return

    try:
        resp = requests.get(
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": coin_id, "vs_currencies": currency},
            timeout=15,
        )
        resp.raise_for_status()
        rate = resp.json().get(coin_id, {}).get(currency)
        if rate is None:
            await update.message.reply_text("Couldn't convert — check the currency code (e.g. usd, eur, ngn).")
            return
        total = rate * amount
        await update.message.reply_text(
            f"{amount} {coin_id} ≈ {total:,.2f} {currency.upper()}"
        )
    except requests.RequestException as e:
        logger.error("convert() error: %s", e)
        await update.message.reply_text("Error converting. Try again shortly.")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is not set.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CommandHandler("chart", chart))
    app.add_handler(CommandHandler("trending", trending))
    app.add_handler(CommandHandler("watch", watch))
    app.add_handler(CommandHandler("unwatch", unwatch))
    app.add_handler(CommandHandler("watchlist", watchlist_cmd))
    app.add_handler(CommandHandler("addholding", add_holding))
    app.add_handler(CommandHandler("removeholding", remove_holding))
    app.add_handler(CommandHandler("portfolio", portfolio_cmd))
    app.add_handler(CommandHandler("convert", convert))

    logger.info("CoinMarketCap66_Bot starting at %s...", datetime.utcnow().isoformat())
    app.run_polling()


if __name__ == "__main__":
    main()
