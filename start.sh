#!/bin/bash

# 1. Download Real Data
echo "Fetching Codeforces Data..."
python fetch_data.py

# 2. Start C++ Engine (It will now read the file we just downloaded)
cd cpp_backend
./cp_engine &
cd ..

# 3. Start Python Frontend
exec gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:8080 app:app