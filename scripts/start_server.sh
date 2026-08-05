#!/bin/bash
# Super Translate autostart (app + frp public tunnel)
# --host 0.0.0.0: LAN access bypasses the throttled frp tunnel.
# PYTHONFAULTHANDLER=1: native crashes (PyMuPDF/pikepdf) leave a Python
# stack in server.log instead of a bare "free(): invalid next size".
cd ~/super_translate || exit 1
tmux has-session -t super_translate 2>/dev/null || tmux new-session -d -s super_translate "PYTHONFAULTHANDLER=1 .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 18001 --workers 1 --no-proxy-headers 2>&1 | tee -a server.log"
tmux has-session -t frpc_web 2>/dev/null || tmux new-session -d -s frpc_web "$HOME/frp_web/frpc -c $HOME/frp_web/frpc_web.toml 2>&1 | tee -a $HOME/frp_web/frpc_web.log"
