#!/bin/sh
# Start Tika Server in the background
java -jar /app/tika-server-standard-2.6.0.jar --host=0.0.0.0 --port=9998 --config /app/tika-config.xml &

# Wait for Tika to be ready
echo "Waiting for Tika server to start..."
while ! nc -z localhost 9998; do
  sleep 1
done
echo "Tika server started."

# Start the FastAPI application
uvicorn main:app --host 0.0.0.0 --port 8000
