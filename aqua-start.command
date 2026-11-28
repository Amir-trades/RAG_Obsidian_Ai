#!/bin/zsh

cd "$(dirname "$0")" || exit 1

open_aqua_app() {
    if [[ -d "$HOME/Applications/Aqua.app" ]]; then
        open -a "$HOME/Applications/Aqua.app"
    elif [[ -d "/Applications/Aqua.app" ]]; then
        open -a "/Applications/Aqua.app"
    else
        open "http://127.0.0.1:8501"
    fi
}

if lsof -nP -iTCP:8501 -sTCP:LISTEN >/dev/null 2>&1; then
    open_aqua_app
    exit 0
fi

nohup "./.venv/bin/streamlit" run src/app.py \
    --server.headless true \
    --server.address 127.0.0.1 \
    --server.port 8501 \
    >/tmp/aqua.log 2>&1 &

echo "$!" >/tmp/aqua.pid

for _ in {1..30}; do
    if curl -fsS "http://127.0.0.1:8501/" >/dev/null 2>&1; then
        open_aqua_app
        exit 0
    fi
    sleep 1
done

echo "Aqua konnte nicht gestartet werden."
echo "Details: /tmp/aqua.log"
exit 1
