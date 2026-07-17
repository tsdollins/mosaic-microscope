FROM python:3.11-trixie
USER root
WORKDIR /root/

# uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Disable development dependencies
ENV UV_NO_DEV=1

# Java runtime for ashlar: pyjnius starts a JVM at import time. curl kept for
# debugging; JAVA_HOME must point at the installed JRE so pyjnius can find it.
RUN apt-get update && apt-get install -y curl openjdk-21-jre-headless && rm -rf /var/lib/apt/lists/*
ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

# get the code
COPY pyproject.toml /root/
COPY uv.lock /root/
COPY .python-version /root/
RUN /bin/uv sync --locked
COPY ./src /root/

# packages
RUN uv sync

# env vars
ARG githash
ENV GITHASH=$githash

ARG repo
ENV REPO=$repo

# Run our flow script when the container starts
CMD uv run python /root/consumer-mosaic-stitcher.py