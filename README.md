# Tagwise

A Telegram bot that tracks top Polymarket traders and sends real-time trade alerts, with automated copy trading and a freemium subscription model.

## Features

- **Wallet Tracking** — Track any Polymarket wallet by address or pick from the leaderboard. Get notified when tracked wallets make trades.
- **Leaderboard** — Browse top traders by category (Politics, Sports, Crypto, Tech, Culture, Finance) and time period (Daily, Weekly, Monthly, All-Time).
- **Copy Trading** — Create an on-chain trading wallet and automatically mirror trades from tracked wallets via the Polymarket Builder API.
- **Wallet Analytics** — View PnL, ROI, win rate, volume, and open positions for any Polymarket wallet.
- **Multi-Buy Alerts** — Detect when multiple tracked wallets buy the same side of a market (PRO).
- **Confidence Scoring** — Score trade signals based on trader history and market conditions (PRO).
- **Referral System** — Share a referral link. Referred users get a 3-day PRO trial; referrers earn 7 days of PRO when their referral subscribes.
- **Crypto Payments** — PRO subscriptions via Paymento (crypto payment gateway).

## Architecture

The bot runs as **3 Docker containers**:

| Service | Container | Description |
|---|---|---|
| **Bot** | `tagwise-bot` | Telegram bot handling user interactions, menus, and commands |
| **Worker** | `tagwise-worker` | Background worker polling tracked wallets for new trades and sending alerts |
| **Leaderboard** | `tagwise-leaderboard` | Refreshes the Polymarket leaderboard every 6 hours |

```
Polymarket APIs → PolymarketClient (with Redis cache)
       ↓
ScheduledTasks (polls wallets every 30s)
       ↓
NotificationService → NotificationQueue (rate-limited) → Telegram
       ↓
CopyTradeManager → BuilderRelayer → Polymarket (on-chain orders)
```

## Tech Stack

- **Python 3.11** with [`python-telegram-bot`](https://github.com/python-telegram-bot/python-telegram-bot) (async)
- **PostgreSQL** via SQLAlchemy async + Alembic migrations
- **Redis** for caching (leaderboard, wallet stats, positions)
- **Web3 / eth-account** for on-chain wallet management
- **Polymarket CLOB Client** ([`py-clob-client`](https://github.com/Polymarket/py-clob-client)) for order execution
- **Polymarket Builder SDK** ([`py-builder-signing-sdk`](https://github.com/nicokimmel/py-builder-signing-sdk)) for volume attribution
- **Paymento** for crypto payment processing
- **Prometheus** for metrics and monitoring
- **Docker Compose** for deployment

## Project Structure

```
Tagwise/
├── main.py                          # Entry point (polling or webhook mode)
├── docker-compose.yml               # Production (3 containers, AWS services)
├── docker-compose.local.yml         # Local development
├── Dockerfile
├── requirements.txt
├── alembic.ini                      # Database migration config
├── migrations/                      # SQL and Python migration scripts
│
├── bot/
│   ├── core.py                      # TagwiseBot class — initialization and handler registration
│   ├── config.py                    # Config, TierLimits, PaymentoConfig
│   ├── constants.py                 # Display constants (time periods, categories)
│   ├── keyboards.py                 # Inline keyboard builders
│   ├── paymento.py                  # Paymento payment gateway integration
│   ├── polymarket_client.py         # Polymarket API client (CLOB, Data API, Gamma)
│   ├── monitoring.py                # Prometheus metrics
│   ├── utils.py                     # Shared utilities
│   │
│   ├── handlers/
│   │   ├── commands.py              # /start, /track, /wallets, /stats, /account, etc.
│   │   ├── callbacks.py             # Inline button callback handlers
│   │   ├── menus.py                 # Menu page display logic
│   │   ├── displays.py              # Shared view rendering (account, upgrade, referral, wallets)
│   │   └── formatters.py            # Message formatting helpers
│   │
│   ├── services/
│   │   ├── database.py              # SQLAlchemy models, queries, referral logic
│   │   ├── cache.py                 # Redis cache manager
│   │   ├── cache_strategies.py      # TTL strategies for different data types
│   │   ├── notifications.py         # Trade alert formatting and delivery
│   │   ├── notification_queue.py    # Rate-limited Telegram message queue
│   │   ├── analytics.py             # Wallet performance analytics
│   │   └── webhooks.py              # Payment webhook handler
│   │
│   ├── trading/
│   │   ├── wallet_manager.py        # Encrypted on-chain wallet (create, import, fund)
│   │   ├── copy_trader.py           # Copy trade execution engine
│   │   ├── builder_relayer.py       # Polymarket Builder/Relayer API (gasless ops)
│   │   └── commands.py              # /copy, /wallet trading commands and menus
│   │
│   └── tasks/
│       └── scheduler.py             # Periodic wallet checks and leaderboard refresh
```

## Free vs PRO

| Feature | Free | PRO |
|---|---|---|
| Custom Wallets | 3 | 100 |
| Leaderboard Traders | 5 | 50 |
| Confidence Scores | ❌ | ✅ |
| Multi-Buy Alerts | ❌ | ✅ |
| Leaderboard Filters | ❌ | ✅ |
| Copy Trading | ✅ | ✅ |
| **Price** | Free | $29/mo or $199/yr |

## Setup

### Prerequisites

- Python 3.11+
- PostgreSQL
- Redis
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- A Polymarket account with Builder API credentials (from [builders.polymarket.com](https://builders.polymarket.com/))

### Environment Variables

Create a `.env` file in the `Tagwise/` directory:

```env
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
ADMIN_USER_IDS=123456789

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/tagwise

# Redis
REDIS_URL=redis://localhost:6379/0

# Polymarket
PRIVATE_KEY=your_polygon_private_key
POLYMARKET_PROXY_ADDRESS=your_proxy_address
POLYMARKET_BUILDER_API_KEY=your_builder_key
POLYMARKET_BUILDER_SECRET=your_builder_secret
POLYMARKET_BUILDER_PASSPHRASE=your_builder_passphrase
POLYGON_RPC=https://polygon-rpc.com

# Paymento (for PRO payments)
PAYMENTO_API_KEY=your_paymento_key
PAYMENTO_SECRET_KEY=your_paymento_secret
WEBHOOK_URL=https://your-domain.com

# Wallet encryption
WALLET_ENCRYPTION_KEY=your_fernet_key

# Mode
ENV=development
```

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run database migrations
alembic upgrade head

# Start the bot (polling mode)
python main.py
```

### Production (Docker)

```bash
# Build and start all services
docker compose up -d --build

# View logs
docker compose logs -f bot
```

The bot runs in **webhook mode** when `ENV=production` (requires `WEBHOOK_URL` and a public HTTPS endpoint). Otherwise it defaults to **polling mode**.

## Bot Commands

| Command | Description |
|---|---|
| `/start` | Welcome screen / referral deep link | Main menu |
| `/toptraders` | Browse leaderboard |
| `/track <address>` | Track a wallet |
| `/wallets` | View tracked wallets |
| `/untrack <address>` | Stop tracking |
| `/stats <address>` | Wallet analytics |
| `/wallet` | Trading wallet |
| `/copy` | Copy trading settings |
| `/account` | Subscription status |
| `/upgrade` | Upgrade to PRO |
| `/referral` | Referral link and rewards |
| `/help` | Command reference |

## License

Private — all rights reserved.
