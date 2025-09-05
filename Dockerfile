# Use an official Python runtime as a parent image
FROM python:3.10-slim-bookworm

RUN sed -i "s@http://deb.debian.org@http://mirrors.aliyun.com@g" /etc/apt/sources.list.d/debian.sources

RUN ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime

ARG PORT=8000
ARG TIKA_VERSION=3.2.2
ARG JRE='openjdk-17-jre-headless'
# Prefer Aliyun mirror for faster downloads in regions where it's closer than the Apache archive
ENV TIKA_SERVER_URL="https://mirrors.aliyun.com/apache/tika/${TIKA_VERSION}/tika-server-standard-${TIKA_VERSION}.jar"
ENV TIKA_VERSION=${TIKA_VERSION}
ENV PORT=${PORT}

# Set the working directory in the container
WORKDIR /app

# Install OpenJDK for Tika Server and netcat for the health check
RUN apt-get update && apt-get install -y \
    openjdk-17-jre-headless \
    netcat-openbsd \
    curl \
    && curl -fSL ${TIKA_SERVER_URL} -o tika-server-standard-${TIKA_VERSION}.jar \
    && rm -rf /var/lib/apt/lists/*

# Copy the application's requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code and the entrypoint script
COPY main.py .
COPY entrypoint.sh .

# Copy the Tika server JAR and the custom config
# COPY tika-server-standard-2.6.0.jar .
COPY tika-config.xml .

# Make the entrypoint script executable
RUN chmod +x entrypoint.sh

# Expose the ports for FastAPI and Tika
EXPOSE ${PORT}
EXPOSE 9998

# Set the entrypoint script to run when the container starts
ENTRYPOINT ["./entrypoint.sh"]