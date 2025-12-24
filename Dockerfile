# --- Stage 1: Build C++ Backend ---
FROM ubuntu:22.04 as cpp-builder
WORKDIR /build

# Install compiler
RUN apt-get update && apt-get install -y g++

# Copy C++ source
COPY cpp_backend/ .

# Compile
# We use -pthread instead of -lws2_32 because Docker runs on Linux!
RUN g++ -O3 main.cpp -o cp_engine -pthread

# --- Stage 2: Final Runtime ---
FROM python:3.9-slim
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy C++ Binary from Stage 1
COPY --from=cpp-builder /build/cp_engine .

# Copy Python App & Templates
COPY . .

# Fix Windows line endings in start.sh (Just in case)
RUN sed -i 's/\r$//' start.sh && chmod +x start.sh

# Environment Config
ENV PORT=8080
EXPOSE 8080

# Start Command
CMD ["./start.sh"]