#!/bin/bash
set -e

echo "Starting Ollama..."
ollama serve &
OLLAMA_PID=$!

# Wait for Ollama API to be ready (Req. 2.1, 2.2)
echo "Waiting for Ollama API..."
for i in {1..60}; do
  if curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
    echo "Ollama API ready"
    break
  fi
  echo "Waiting for Ollama... ($i/60)"
  sleep 1
done

# Pre-pull model (Req. 2.1, 2.2) - use smaller model if qwen-vl:7b-q4 is not available
echo "Pre-pulling model qwen-vl:7b-q4 or fallback..."
if ! timeout 600 ollama pull qwen-vl:7b-q4 2>&1 | grep -q "Error"; then
  echo "Model qwen-vl:7b-q4 pulled successfully"
else
  echo "Attempting fallback to llava:7b-q4..."
  timeout 300 ollama pull llava:7b-q4 || echo "Model pull timed out or failed; will retry on first use"
fi

# Start Gunicorn (exec replaces the shell, PID 1)
echo "Starting Gunicorn..."
exec "$@"
