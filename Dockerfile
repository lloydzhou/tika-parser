# Use an official Python runtime as a parent image
FROM python:3.10-slim-bookworm

# Set the working directory in the container
WORKDIR /app

# Install OpenJDK for Tika Server and netcat for the health check
RUN apt-get update && apt-get install -y \
    openjdk-17-jre-headless \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

# Copy the Tika server JAR and the custom config
COPY tika-server-standard-2.6.0.jar .
COPY tika-config.xml .

# Copy the application's requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code and the entrypoint script
COPY main.py .
COPY entrypoint.sh .

# Make the entrypoint script executable
RUN chmod +x entrypoint.sh

# Expose the ports for FastAPI and Tika
EXPOSE 8000
EXPOSE 9998

# Set the entrypoint script to run when the container starts
ENTRYPOINT ["./entrypoint.sh"]