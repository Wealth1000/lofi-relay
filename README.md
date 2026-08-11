# Lofi Relay

A small, private audio relay service for livestreams.

Lofi Relay takes a YouTube livestream that exposes only combined video+audio HLS streams, extracts the stream URL with `yt-dlp`, removes the video server-side with FFmpeg, and exposes the resulting audio as an HTTP stream.

This means the client does **not** download the video portion of the livestream.

```text
YouTube livestream
       │
       │ HLS (video + audio)
       ▼
    yt-dlp
       │
       ▼
    FFmpeg
       │
       │ video discarded
       ▼
  AAC audio stream
       │
       ▼
    FastAPI
       │
       │ authenticated HTTP
       ▼
      mpv
```

## Features

* Audio-only relay for YouTube livestreams
* Server-side video removal
* No audio transcoding (`-c:a copy`)
* Multiple named livestream endpoints
* API-key authentication
* Docker support
* Configuration-driven stream list
* Designed for private/personal use

## Current streams

The service currently exposes:

| Endpoint                  | Stream                              |
| ------------------------- | ----------------------------------- |
| `/lofi/relax-study`       | Lofi Girl — beats to relax/study to |
| `/lofi/chill-game`        | Lofi Girl — beats to chill/game to  |
| `/lofi/asian-relax-study` | Asian-style relax/study             |
| `/lofi/jazz-chill-study`  | Jazz/chill/study                    |
| `/lofi/sleep-dream`       | Sleep/dream                         |
| `/lofi/rain-sad`          | Rain/sad                            |
| `/lofi/piano-focus`       | Piano/focus                         |

The actual YouTube URLs are configured in:

```text
app/config.py
```

Adding another stream does not require changing the streaming logic.

---

# Requirements

For local development you need:

* Git
* Python 3.12+ (3.14 is currently used)
* `pip`
* FFmpeg
* `yt-dlp`
* Docker (optional, but recommended for deployment testing)

You will also need a GitHub account if you intend to deploy from the repository.

---

# 1. Clone the repository

Clone the private repository:

```bash
git clone https://github.com/YOUR_USERNAME/lofi-relay.git
cd lofi-relay
```

---

# 2. Create a Python virtual environment

Create the virtual environment:

```bash
python3 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

Your shell should now show something similar to:

```text
(.venv)
```

---

# 3. Install Python dependencies

Install the requirements:

```bash
pip install -r requirements.txt
```

If setting up the project from scratch and `requirements.txt` does not yet exist, install the dependencies manually:

```bash
pip install fastapi 'uvicorn[standard]' yt-dlp python-dotenv
```

Then generate the requirements file:

```bash
pip freeze > requirements.txt
```

> If you use `zsh`, remember to quote `'uvicorn[standard]'`. Otherwise zsh may interpret the square brackets as globbing syntax.

---

# 4. Install FFmpeg

FFmpeg must be installed separately because it is an operating-system dependency rather than a Python package.

### Fedora

```bash
sudo dnf install ffmpeg
```

### Debian / Ubuntu

```bash
sudo apt install ffmpeg
```

Verify:

```bash
ffmpeg -version
```

---

# 5. Configure the API key

The service uses a private API key to prevent unauthorized users from accessing the streams.

The key is **not stored in the repository**.

Generate a strong key:

```bash
openssl rand -hex 32
```

Create a `.env` file in the project root:

```bash
nano .env
```

Add:

```env
LOFI_API_KEY=PASTE_YOUR_GENERATED_KEY_HERE
```

For example:

```env
LOFI_API_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
```

Do **not** commit this file.

The repository's `.gitignore` already excludes:

```text
.env
```

---

# 6. Configure streams

Streams are defined in:

```text
app/config.py
```

The configuration looks like:

```python
UPSTREAM_FORMAT = "91"

STREAMS = {
    "relax-study": "https://www.youtube.com/watch?v=X4VbdwhkE10",
    "chill-game": "https://www.youtube.com/live/4xDzrJKXOOY",
    "asian-relax-study": "https://www.youtube.com/live/1Tl2FtV06qo",
    "jazz-chill-study": "https://www.youtube.com/live/E2vONfzoyRI",
    "sleep-dream": "https://www.youtube.com/live/VAlMDl00mYY",
    "rain-sad": "https://www.youtube.com/live/CwPCy1GLS38",
    "piano-focus": "https://www.youtube.com/live/N0snMcR6aaA",
}
```

The stream name becomes the API endpoint.

For example:

```text
/lofi/relax-study
```

maps to:

```python
STREAMS["relax-study"]
```

The upstream format is kept separately:

```python
UPSTREAM_FORMAT = "91"
```

This means the format can be changed globally without modifying every stream entry.

---

# 7. Run locally

From the **project root** (`lofi-relay/`), start FastAPI:

```bash
uvicorn app.main:app --reload
```

Do not run this from inside `app/`.

The server should be available at:

```text
http://127.0.0.1:8000
```

---

# 8. Test the health endpoint

The health endpoint does not require authentication:

```bash
curl http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok"}
```

---

# 9. Test authentication

Without an API key:

```bash
curl -i http://127.0.0.1:8000/lofi/relax-study
```

Expected:

```text
HTTP/1.1 401 Unauthorized
```

Now load the key from `.env` into the current shell:

```bash
export LOFI_API_KEY="$(grep '^LOFI_API_KEY=' .env | cut -d= -f2-)"
```

You can verify that it loaded without printing the secret:

```bash
[[ -n "$LOFI_API_KEY" ]] && echo "API key loaded"
```

Then test the authenticated endpoint:

```bash
curl -i \
  -H "X-API-Key: $LOFI_API_KEY" \
  http://127.0.0.1:8000/lofi/relax-study
```

The response should be:

```text
HTTP/1.1 200 OK
content-type: audio/aac
```

Do not allow `curl` to dump the binary audio into your terminal.

---

# 10. Play through mpv

With the server running:

```bash
mpv \
  --no-video \
  --really-quiet \
  --http-header-fields="X-API-Key: $LOFI_API_KEY" \
  http://127.0.0.1:8000/lofi/relax-study
```

If everything is working, the livestream audio should play through mpv.

Other stations can be played by changing the endpoint:

```bash
mpv \
  --no-video \
  --really-quiet \
  --http-header-fields="X-API-Key: $LOFI_API_KEY" \
  http://127.0.0.1:8000/lofi/chill-game
```

---

# Docker

Docker provides a self-contained environment containing Python, FFmpeg, yt-dlp, and the application.

The API key remains outside the image.

## Build the image

From the project root:

```bash
docker build -t lofi-relay .
```

## Run the container

Pass the API key through `.env`:

```bash
docker run --rm \
  --env-file .env \
  -p 8000:8000 \
  lofi-relay
```

The service will be available at:

```text
http://127.0.0.1:8000
```

Test:

```bash
curl http://127.0.0.1:8000/health
```

Then play a stream:

```bash
export LOFI_API_KEY="$(grep '^LOFI_API_KEY=' .env | cut -d= -f2-)"

mpv \
  --no-video \
  --really-quiet \
  --http-header-fields="X-API-Key: $LOFI_API_KEY" \
  http://127.0.0.1:8000/lofi/relax-study
```

---

# Project structure

```text
lofi-relay/
│
├── app/
│   ├── __init__.py
│   ├── auth.py
│   ├── config.py
│   ├── main.py
│   │
│   └── services/
│       ├── __init__.py
│       ├── stream.py
│       └── youtube.py
│
├── .dockerignore
├── .gitignore
├── Dockerfile
├── README.md
├── requirements.txt
└── .env                 # local only — NOT committed
```

## Architecture

### `config.py`

Contains the available streams and upstream format:

```text
stream name → YouTube URL
```

### `youtube.py`

Responsible for communicating with `yt-dlp` and obtaining the actual HLS stream URL.

```text
YouTube URL
    ↓
yt-dlp
    ↓
HLS URL
```

### `stream.py`

Starts FFmpeg and removes the video stream.

Conceptually:

```text
HLS video + audio
       ↓
     FFmpeg
       ↓
    -vn
       ↓
   AAC audio
```

The audio is copied rather than transcoded:

```text
-c:a copy
```

### `auth.py`

Validates the `X-API-Key` HTTP header against `LOFI_API_KEY`.

### `main.py`

Provides the HTTP API and connects all the components together.

---

# API

## Health

```http
GET /health
```

No authentication required.

Response:

```json
{
  "status": "ok"
}
```

## Stream

```http
GET /lofi/{stream_name}
```

Requires:

```http
X-API-Key: YOUR_API_KEY
```

Example:

```http
GET /lofi/relax-study
X-API-Key: YOUR_API_KEY
```

Response:

```text
audio/aac
```

---

# Security

The API key should never be committed to Git.

The following file is intentionally ignored:

```text
.env
```

For local development:

```env
LOFI_API_KEY=...
```

For deployment, configure the same variable through the hosting provider's environment/secrets configuration.

Do not put the API key in:

* `config.py`
* `main.py`
* `auth.py`
* `Dockerfile`
* Git
* shell scripts committed to the repository

---

# Adding another station

Add an entry to `app/config.py`:

```python
STREAMS = {
    "relax-study": "https://www.youtube.com/watch?v=X4VbdwhkE10",
    "coding": "https://www.youtube.com/your-new-stream",
}
```

The new endpoint automatically becomes:

```text
/lofi/coding
```

No changes to `main.py`, `youtube.py`, or `stream.py` are necessary.

---

# Troubleshooting

## `ModuleNotFoundError: No module named 'app'`

Make sure Uvicorn is being started from the project root:

```text
lofi-relay/
```

Run:

```bash
uvicorn app.main:app --reload
```

not from:

```text
lofi-relay/app/
```

---

## `zsh: no matches found: uvicorn[standard]`

Quote the package name:

```bash
pip install 'uvicorn[standard]'
```

---

## `401 Unauthorized`

Check that the API key was supplied:

```bash
echo "${LOFI_API_KEY:+API key is loaded}"
```

Then:

```bash
mpv \
  --http-header-fields="X-API-Key: $LOFI_API_KEY" \
  --no-video \
  http://127.0.0.1:8000/lofi/relax-study
```

---

## FFmpeg not found

Check:

```bash
ffmpeg -version
```

Install FFmpeg using your operating system's package manager.

---

## YouTube format changes

The upstream format is currently:

```python
UPSTREAM_FORMAT = "91"
```

If YouTube changes the available HLS formats, inspect them with:

```bash
yt-dlp -F "YOUTUBE_URL"
```

Then update `UPSTREAM_FORMAT` accordingly.

---

# Deployment

The application is designed to run as a Docker container.

The intended deployment architecture is:

```text
Private GitHub repository
          ↓
      Host platform
          ↓
    Docker container
          ↓
 ┌────────┴────────┐
 │                 │
FastAPI          FFmpeg
 │                 │
 └───────┬─────────┘
         │
       yt-dlp
         │
         ▼
      YouTube
```

The production API key should be configured as an environment variable:

```text
LOFI_API_KEY
```

The `.env` file should **not** be uploaded to the deployment platform or committed to Git.

---

# Development workflow

After making changes:

```bash
git status
```

Review the changes:

```bash
git diff
```

Stage:

```bash
git add .
```

Commit:

```bash
git commit -m "Describe the change"
```

Push:

```bash
git push
```

Before pushing, make sure `.env` is not tracked:

```bash
git status
```

---

# Purpose

Lofi Relay exists for one simple purpose:

> **Turn livestreams that only expose combined video/audio HLS streams into lightweight audio-only HTTP streams.**

The client only receives the audio it actually needs.

The service is intentionally small and modular. There is no database, user-management system, queue, caching layer, or other infrastructure that isn't necessary for its intended private use.
> **Currently, development is only focused on Lo-Fi Girl livestreams. It should technically work for others, but has only been tested on Lo-Fi Girl livestreams**
