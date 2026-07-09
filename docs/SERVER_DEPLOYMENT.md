# Portfolio Operations Server Deployment

This project runs six user-systemd services in production:

- `portfolio-ops-platform-api.service`
- `portfolio-ops-platform-web.service`
- `portfolio-ops-watchlist-api.service`
- `portfolio-ops-watchlist-web.service`
- `portfolio-ops-portfolio-api.service`
- `portfolio-ops-portfolio-web.service`

The market-data timer is installed separately as:

- `portfolio-ops-market-data-refresh.service`
- `portfolio-ops-market-data-refresh.timer`

## Install App Services

Build the three frontend bundles before installing web services:

```bash
npm --prefix apps/platform/frontend ci
npm --prefix apps/platform/frontend run build
npm --prefix apps/watchlist/frontend ci
npm --prefix apps/watchlist/frontend run build
npm --prefix apps/portfolio/frontend ci
npm --prefix apps/portfolio/frontend run build
```

Install Python dependencies into the project virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e packages/instrument-core/python
.venv/bin/python -m pip install -e apps/platform/backend
.venv/bin/python -m pip install -e apps/watchlist/backend
.venv/bin/python -m pip install -e apps/portfolio/backend
```

Install and start the app units:

```bash
PROJECT_ROOT="$PWD" PYTHON_BIN="$PWD/.venv/bin/python" \
  infra/systemd/install_app_services.sh
```

Install and start the market-data timer:

```bash
PROJECT_ROOT="$PWD" BACKEND_ROOT="$PWD/apps/platform/backend" PYTHON_BIN="$PWD/.venv/bin/python" \
  infra/systemd/install_market_data_refresh_timer.sh
```

## Ports

- Platform API: `8102`
- Platform Web: `3100`
- Watchlist API: `8100`
- Watchlist Web: `3101`
- Portfolio API: `8101`
- Portfolio Web: `3102`

## Health Checks

```bash
curl --noproxy '*' -fsS http://127.0.0.1:8102/api/health
curl --noproxy '*' -fsS http://127.0.0.1:8100/api/watchlists
curl --noproxy '*' -fsS http://127.0.0.1:8101/api/portfolios
curl --noproxy '*' -fsS http://127.0.0.1:3100/
curl --noproxy '*' -fsS http://127.0.0.1:3101/
curl --noproxy '*' -fsS http://127.0.0.1:3102/
```
