#!/usr/bin/env bash
set -e

echo "=== Setting up BPSC Telegram Quiz Bot on Ubuntu ==="

sudo apt update
sudo apt install -y python3-pip python3-venv git

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Installing requirements..."
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

if [ ! -f ".env" ]; then
    echo "Creating .env file..."
    read -p "Enter your Telegram Bot Token: " bot_token
    echo "TELEGRAM_BOT_TOKEN=$bot_token" > .env
    echo ".env created successfully."
fi

echo "Installing systemd service..."
sudo cp bpsc_bot.service /etc/systemd/system/bpsc_bot.service
sudo systemctl daemon-reload
sudo systemctl enable bpsc_bot
sudo systemctl restart bpsc_bot

echo "=== Bot is now RUNNING 24/7! ==="
sudo systemctl status bpsc_bot --no-pager
