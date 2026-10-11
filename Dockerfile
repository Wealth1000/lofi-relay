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

# Browser impersonation is what keeps YouTube from rate-limiting this server's
# shared egress IP, and curl_cffi + yt-dlp must agree on a supported version
# range for it to load. A disagreement surfaces only as a silent ImportError
# inside yt-dlp, which then falls back to a default fingerprint and the 429s
# come back with no sign that the dependency broke. Fail the build instead.
RUN python -c "from yt_dlp.YoutubeDL import YoutubeDL; from yt_dlp.networking.impersonate import ImpersonateTarget; YoutubeDL({'impersonate': ImpersonateTarget.from_str('chrome')}); print('browser impersonation OK')"

COPY app ./app

# Cookies are no longer baked into the image. They live in a private GitHub
# Gist and are pulled at cold boot, so YouTube rotating session cookies never
# requires rebuilding or redeploying this image.
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
