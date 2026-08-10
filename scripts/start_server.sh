#!/bin/bash
# Super Translate autostart (loopback only; use Whalent or an SSH tunnel).
# PYTHONFAULTHANDLER=1: native crashes (PyMuPDF/pikepdf) leave a Python
# stack in server.log instead of a bare "free(): invalid next size".
cd ~/super_translate || exit 1
tmux has-session -t super_translate 2>/dev/null || tmux new-session -d -s super_translate "PYTHONFAULTHANDLER=1 .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18001 --workers 1 --no-proxy-headers 2>&1 | tee -a server.log"
