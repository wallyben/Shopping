# Pokemon Center UK Restock Monitor

Monitors product pages on Pokemon Center UK and alerts you the moment stock
appears. You complete checkout manually — the browser opens automatically on
your machine the instant a restock is detected.

---

## What it does

- Polls product pages at a configurable interval (default: every 10s when OOS, 30s when in stock)
- Detects stock state changes via HTML parsing and JSON-LD structured data
- Fires alerts via **Discord webhook** and/or **Telegram** simultaneously
- Opens the product URL in your browser automatically on restock
- Prints a large terminal alert so you cannot miss it
- Handles rate limits, network errors, and Cloudflare challenges gracefully

## What it does NOT do

- It does not add to cart or complete checkout automatically
- It does not store payment details
- It does not use proxies unless you configure one

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` and fill in at least one notification channel.

### 3. Add products to monitor

Edit `skus.json`. Each entry:

```json
[
  {
    "name": "Scarlet & Violet Elite Trainer Box",
    "url": "https://www.pokemoncenter.com/en-gb/product/290-80571/...",
    "sku": "290-80571",
    "enabled": true
  }
]
```

- `url`: copy the full product URL from Pokemon Center UK
- `sku`: the product code (from the URL or product page)
- `enabled`: set to `true` to monitor, `false` to pause without deleting

### 4. Run

```bash
python main.py
```

Verbose output:

```bash
python main.py --log-level DEBUG
```

Stop with Ctrl+C.

---

## Getting a Discord Webhook URL

1. Open your Discord server settings
2. Integrations > Webhooks > New Webhook
3. Copy the URL and paste into `.env` as `DISCORD_WEBHOOK_URL`

## Getting Telegram credentials

1. Message `@BotFather` on Telegram, create a bot, copy the token
2. Message `@userinfobot` to get your chat ID
3. Paste both into `.env`

---

## Finding product URLs

Navigate to any product on `pokemoncenter.com/en-gb`, copy the URL from your
browser address bar, and paste it into `skus.json`.

---

## Configuration reference

| Variable | Default | Description |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | — | Discord webhook (optional) |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token (optional) |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID (optional) |
| `POLL_INTERVAL_SECONDS` | 30 | Check interval when product is in stock |
| `POLL_INTERVAL_FAST_SECONDS` | 10 | Check interval when product is OOS |
| `POLL_JITTER_MAX_SECONDS` | 5 | Max random delay added to each poll |
| `AUTO_OPEN_BROWSER` | 1 | Open browser automatically on restock (1=yes, 0=no) |
| `HTTP_PROXY` | — | Optional HTTP proxy URL |

---

## Troubleshooting

**"UNKNOWN state" warnings appear repeatedly**
The site structure may have changed and the HTML parser cannot find stock
indicators. Check `monitor/detector.py` and update the CSS selectors or
text patterns.

**Cloudflare challenge detected**
The IP is being challenged. Wait a few minutes, or configure an HTTP proxy
in `.env`. At very high poll frequencies this becomes more likely — keep
`POLL_INTERVAL_FAST_SECONDS` at 10 or higher.

**No alerts received**
Check your `.env` values. Run with `--log-level DEBUG` to see exactly what
is being sent and any API errors.
