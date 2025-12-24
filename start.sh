#!/bin/bash
# 1. Start the C++ Backend in the background
./cp_engine &

# 2. Start the Python Frontend
# Using gunicorn with eventlet for WebSocket support
exec gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:8080 app:app