#!/usr/bin/env bash
# Avvia backend + interfaccia (compilata) sulla porta 8000.  Uso:  ./start.sh [--lan]
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
if [ ! -d web/dist ]; then (cd web && npm install --no-audit --no-fund && npm run build); fi
[ -f .env ] && set -a && . ./.env && set +a
HOST=127.0.0.1
if [ "$1" = "--lan" ]; then
  HOST=0.0.0.0
  [ -n "$MODEL3D_TOKEN" ] || { echo "Per --lan imposta MODEL3D_TOKEN in .env (le foto di persone non vanno esposte in chiaro sulla rete)."; exit 1; }
fi
exec python -m uvicorn server.app.main:app --host "$HOST" --port 8000
