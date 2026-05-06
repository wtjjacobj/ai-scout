#!/bin/bash
cd ~/Projects/ai-scout
ENRICHED=$(sqlite3 data/ai_scout.db "SELECT COUNT(*) FROM projects WHERE is_active=1 AND product_type IS NOT NULL")
TOTAL=$(sqlite3 data/ai-scout.db "SELECT COUNT(*) FROM projects WHERE is_active=1")
PENDING=$((TOTAL - ENRICHED))
PCT=$(echo "scale=1; $ENRICHED * 100 / $TOTAL" | bc)
echo "📊 Enrich progress: ${ENRICHED}/${TOTAL} (${PCT}%) — ${PENDING} pending"
