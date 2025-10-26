#!/usr/bin/env bash
set -o errexit  # Stop on first error

echo "🔥 Installing system dependencies..."
apt-get update && apt-get install -y qpdf libqpdf-dev poppler-utils

echo "🚀 Upgrading pip and installing Python deps..."
pip install --upgrade pip
pip install -r requirements.txt

echo "✅ Build complete!"
