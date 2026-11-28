#!/bin/zsh

pids=$(lsof -nP -tiTCP:8501 -sTCP:LISTEN 2>/dev/null)

if [[ -n "$pids" ]]; then
    for pid in ${(f)pids}; do
        kill "$pid" 2>/dev/null
    done
fi

if [[ -f /tmp/aqua.pid ]]; then
    saved_pid=$(cat /tmp/aqua.pid)
    kill "$saved_pid" 2>/dev/null
    rm -f /tmp/aqua.pid
fi

echo "Aqua wurde gestoppt."
