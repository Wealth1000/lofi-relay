FROM python:3.14-slim

WORKDIR /app

# System dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno
RUN curl -fsSL https://deno.land/install.sh | sh \
    && mv /root/.deno/bin/deno /usr/local/bin/deno \
    && deno --version

# Deno needs a writable cache on Render
ENV DENO_DIR=/tmp/deno
RUN mkdir -p /tmp/deno

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Cookies are no longer baked into the image. They live in a private GitHub
# Gist and are pulled at cold boot, so YouTube rotating session cookies never
# requires rebuilding or redeploying this image.
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
