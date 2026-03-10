#!/bin/bash
# ─────────────────────────────────────────
# GlowUp AI — Start Script
# ─────────────────────────────────────────

# Load .env if it exists
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
  echo "✓ Loaded .env file"
fi

# Verify API key
if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "⚠️  Warning: ANTHROPIC_API_KEY is not set!"
  echo "   Text features (bio analysis, messaging, ranking) will not work."
  echo "   Get your API key at: https://console.anthropic.com/"
fi

if [ -z "$FAL_KEY" ]; then
  echo "⚠️  Warning: FAL_KEY is not set!"
  echo "   Photo generation will not work."
  echo "   Get your API key at: https://fal.ai/dashboard"
fi

# Start the server
python3 server.py
