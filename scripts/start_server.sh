#!/bin/bash
# Super Translate autostart (app + frp public tunnel)
cd ~/super_translate || exit 1
tmux has-session -t super_translate 2>/dev/null || tmux new-session -d -s super_translate ".venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18001 --workers 1 --no-proxy-headers 2>&1 | tee -a server.log"
tmux has-session -t frpc_web 2>/dev/null || tmux new-session -d -s frpc_web "$HOME/frp_web/frpc -c $HOME/frp_web/frpc_web.toml 2>&1 | tee -a $HOME/frp_web/frpc_web.log"
