#!/bin/bash
set -e
cd ~/Projects/ai-scout

echo "=== Rebuilding TF-IDF index ==="
.venv/bin/python -m ai_scout.hermes.embed index

echo "=== Git push ==="
git add -A
git commit -m "full LLM enrich v0.4: 2222 projects via GLM-5.1 + quality gate >= 50" || true
git push origin main

echo "=== VPS redeploy ==="
sshpass -p 'HmsBzSN8Vh6Xe04u32' ssh -o StrictHostKeyChecking=no root@23.94.136.124 '
cd /opt/ai-scout
git pull
docker stop ai-scout-mcp 2>/dev/null
docker rm ai-scout-mcp 2>/dev/null
docker build -t ai-scout .
docker run -d --name ai-scout-mcp --restart unless-stopped -p 8765:8080 -v /opt/ai-scout/data:/app/data ai-scout
docker ps | grep ai-scout
'

echo "=== DONE ==="
