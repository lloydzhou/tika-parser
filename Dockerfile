FROM python:3.10-slim-bookworm

RUN sed -i "s@http://deb.debian.org@http://mirrors.aliyun.com@g" /etc/apt/sources.list.d/debian.sources

RUN ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime

ARG TIKA_VERSION=3.0.0
ARG JRE='openjdk-17-jre-headless'

ENV TIKA_SERVER_URL="https://archive.apache.org/dist/tika/${TIKA_VERSION}/tika-server-standard-${TIKA_VERSION}.jar"
ENV TIKA_VERSION="${TIKA_VERSION}"

RUN set -eux \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
        gnupg2 ca-certificates $JRE \
        gdal-bin \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-ita \
        tesseract-ocr-fra \
        tesseract-ocr-spa \
        tesseract-ocr-deu \
        tesseract-ocr-chi-sim \
        xfonts-utils \
        fonts-freefont-ttf \
        fonts-liberation \
        wget \
        cabextract \
    && apt-get clean -y \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* \
    && wget -t 10 --max-redirect 1 --retry-connrefused $TIKA_SERVER_URL -O /tika-server-standard-${TIKA_VERSION}.jar \
    && pip3 install regex tika tornado --break-system-packages

ADD ./main.py /main.py
ADD ./text_splitter.py /text_splitter.py

EXPOSE 9998 8888

ENTRYPOINT [ "/bin/sh", "-c", "python3 /main.py & exec java -cp \"/tika-server-standard-${TIKA_VERSION}.jar:/tika-extras/*\" org.apache.tika.server.core.TikaServerCli -h 0.0.0.0 $0 $@"]