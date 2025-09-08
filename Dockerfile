FROM maven:latest as builder

ADD tika /tika

RUN cd /tika && mvn -Dossindex.skip -DskipTests -Dcheckstyle.skip=true -am -pl :tika-server-standard clean package

# Use an official Python runtime as a parent image
FROM python:3.10-slim-bookworm

RUN sed -i "s@http://deb.debian.org@http://mirrors.aliyun.com@g" /etc/apt/sources.list.d/debian.sources

RUN ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime

ARG PORT=8888
ARG TIKA_VERSION=4.0.0-SNAPSHOT
ENV TIKA_VERSION=${TIKA_VERSION}
ENV PORT=${PORT}

# Set the working directory in the container
WORKDIR /app

# Install OpenJDK for Tika Server and netcat for the health check
RUN apt-get update && apt-get install -y \
    openjdk-17-jre-headless \
    netcat-openbsd \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /tika/tika-server/tika-server-standard/target/tika-server-standard-4.0.0-SNAPSHOT.jar /app/

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