#!/usr/bin/env bash
set -e

# Python deps
pip install -r requirements.txt

# Node deps + Tailwind build
npm ci || npm install
npm run build:css
