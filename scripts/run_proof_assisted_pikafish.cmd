@echo off
setlocal
cd /d "%~dp0.."
python tools\proof_uci.py --max-ply 2 --node-limit 100000 --proof-store database\pikafish_proofs.sqlite --fallback-uci-engine "cmd.exe /d /s /c scripts\run_pikafish_baseline.cmd" --fallback-uci-depth 2 --fallback-uci-multipv 1 --fallback-uci-option Threads=16 --fallback-uci-option Hash=1024 --fallback-uci-timeout 4 --direct-fallback-uci
