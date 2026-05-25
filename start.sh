#!/bin/bash
# Start the Pump.fun Trading Bot

echo "═══════════════════════════════════════"
echo "  PUMP.FUN TRADING BOT"
echo "═══════════════════════════════════════"

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
    echo "✓ Environment loaded"
else
    echo "⚠ No .env file found! Copy .env.example to .env and fill in your keys."
    exit 1
fi

# Create data directory
mkdir -p data

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 not found!"
    exit 1
fi

echo "✓ Python: $(python3 --version)"

# Install dependencies if needed
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q -r requirements.txt

echo "✓ Dependencies installed"
echo "═══════════════════════════════════════"
echo "Starting bot..."
echo ""

# Run the bot
python3 bot.py
