#!/bin/bash
# Double-click in Finder to open Ingest Sorter.
# Keep this Terminal window open while you work; close it (or press Ctrl-C) to stop.
cd "$(dirname "$0")"
python3 sorter/server.py
