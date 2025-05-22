#!/bin/bash
set -e
if [ ! -f 'app.py' ]; then
  echo 'Only run from within the dir'
  exit 1
fi
if [ ! -d "venv" ]; then
  virtualenv --python=python3.10 venv
fi
source venv/bin/activate
pip install -r requirements.txt

quart run --port 5001

