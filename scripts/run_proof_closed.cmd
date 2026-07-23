@echo off
setlocal
cd /d "%~dp0.."
python tools\proof_uci.py --closed --max-ply 2 --node-limit 100000 --local-search-depth 2 --local-search-node-limit 5000 --proof-store database\closed_proofs.sqlite --save-online-proofs
