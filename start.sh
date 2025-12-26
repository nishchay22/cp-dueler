#!/bin/bash

echo "--- STARTUP DIAGNOSTICS ---"
ls -la 

echo "--- FETCHING DATA ---"
# FIX: Changed 'fetch_data.py' to 'load_data.py'
if [ -f "load_data.py" ]; then
    python load_data.py
else
    echo "ERROR: load_data.py NOT FOUND in $(pwd)"
fi

# Check if the JSON was created
if [ -f "cpp_backend/problems.json" ]; then
    echo "SUCCESS: problems.json created."
else
    echo "CRITICAL: problems.json missing. C++ engine will contain 0 problems."
fi

echo "--- STARTING C++ ENGINE ---"
cd cpp_backend
./cp_engine &
cd ..

echo "--- STARTING FLASK ---"
exec gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:8080 app:app