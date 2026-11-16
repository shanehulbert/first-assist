#!/bin/bash
# Double-click in Finder to check that everything Ingest Sorter needs is installed.
cd "$(dirname "$0")"
python3 sorter/check_setup.py
echo "You can close this window."
