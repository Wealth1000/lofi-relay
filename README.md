# Lofi Relay

A small, private audio relay service for livestreams.

Lofi Relay takes a YouTube livestream, extracts its HLS stream with `yt-dlp`, removes the video server-side with FFmpeg, and exposes the resulting audio as an HTTP stream.

The client therefore receives **audio only** rather than downloading the video portion of the livestream.

```text
YouTube livestream
       │
       │ HLS
       ▼
    yt-dlp
       │
       │ + YouTube cookies
       │ + Deno / EJS challenge solver
       ▼
    HLS stream
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
* Deno JavaScript runtime for `yt-dlp`
* YouTube cookie support
* Configuration-driven stream list
* Designed for private/personal use

---

# Current streams

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
* Python 3.12+
* `pip`
* FFmpeg
* `yt-dlp`
* Deno
* A YouTube cookies file
* Docker (optional, but recommended for deployment testing)

You will also need a GitHub account if you intend to deploy from the repository.

---

# 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/lofi-relay.git
cd lofi-relay
```

---

# 2. Create a Python virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Your shell should now show something similar to:

```text
(.venv)
```

---

# 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

The project uses:

```text
yt-dlp[default]
```

rather than the bare `yt-dlp` package because the default extras include components used by the YouTube extractor.

If setting up the project from scratch and `requirements.txt` does not yet exist:

```bash
pip install fastapi 'uvicorn[standard]' yt-dlp python-dotenv
```

Then:

```bash
pip freeze > requirements.txt
```

> If you use `zsh`, quote `'uvicorn[standard]'`. Otherwise zsh may interpret the square brackets as globbing syntax.

---

# 4. Install FFmpeg

FFmpeg is an operating-system dependency rather than a Python package.

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

# 5. Install Deno

`yt-dlp` uses a JavaScript runtime to solve YouTube's current JavaScript challenges.

This project uses **Deno**.

Verify whether it is already installed:

```bash
deno --version
```

For example:

```text
deno 2.9.5
```

If Deno is not installed, install it using the official Deno installation instructions.

The Docker image installs Deno automatically, so this step is only necessary for local development.

---

# 6. Configure the API key

The service uses a private API key to prevent unauthorized access.

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

The `.gitignore` should exclude:

```text
.env
youtube-cookies.txt
```

---

# 7. Configure YouTube cookies

YouTube may require authentication before `yt-dlp` is allowed to obtain livestream formats.

Lofi Relay therefore supports a Netscape-format YouTube cookies file:

```text
youtube-cookies.txt
```

## Create the cookies file

1. Open Firefox.
2. Sign into YouTube using the Google account you want to use for the relay.
3. Export the YouTube cookies using a reputable browser cookie-export extension.
4. Save the exported file as:

```text
youtube-cookies.txt
```

Place it in the project root:

```text
lofi-relay/
├── youtube-cookies.txt
├── app/
├── Dockerfile
└── ...
```

The cookie file is a **secret**.

Do not:

* commit it to Git
* upload it to GitHub
* put it inside the Docker image
* send it to other people
* paste its contents into logs or chat

Verify that Git is ignoring it:

```bash
git status --short --ignored youtube-cookies.txt
```

You should see:

```text
!! youtube-cookies.txt
```

### Cookie lifetime

There is no guaranteed refresh interval.

YouTube session cookies can remain valid for a long time, but they can also become invalid earlier if the session is revoked, the account is signed out, YouTube invalidates the session, or other security changes occur.

If `yt-dlp` starts reporting authentication or bot-verification errors again, export a fresh cookies file and replace the old one.

---

# 8. Configure streams

Streams are defined in:

```text
app/config.py
```

For example:

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

---

# 9. Run locally

From the **project root**:

```bash
uvicorn app.main:app --reload
```

Do not run this from inside `app/`.

The server should be available at:

```text
http://127.0.0.1:8000
```

Make sure the application can access the configured cookies file before testing a YouTube stream.

---

# 10. Test the health endpoint

```bash
curl http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok"}
```

---

# 11. Test authentication

Without an API key:

```bash
curl -i http://127.0.0.1:8000/lofi/relax-study
```

Expected:

```text
HTTP/1.1 401 Unauthorized
```

Load the key from `.env`:

```bash
export LOFI_API_KEY="$(grep '^LOFI_API_KEY=' .env | cut -d= -f2-)"
```

Verify that it loaded without printing the secret:

```bash
[[ -n "$LOFI_API_KEY" ]] && echo "API key loaded"
```

Then:

```bash
curl -i \
  -H "X-API-Key: $LOFI_API_KEY" \
  http://127.0.0.1:8000/lofi/relax-study
```

Expected:

```text
HTTP/1.1 200 OK
content-type: audio/aac
```

Do not allow `curl` to dump the binary audio into your terminal.

---

# 12. Play through mpv

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

Docker provides a self-contained environment containing:

* Python
* FFmpeg
* Deno
* yt-dlp
* the Lofi Relay application

The API key and YouTube cookies remain **outside the Docker image**.

## Build the image

From the project root:

```bash
docker build -t lofi-relay .
```

## Run the container

Because the YouTube cookies are a secret, mount them into the container rather than copying them into the image.

On Fedora and other SELinux-enabled systems, use the `:Z` mount option:

```bash
docker run -d \
  --name lofi-relay \
  -p 8000:8000 \
  --env-file .env \
  -v "$(pwd)/youtube-cookies.txt:/run/secrets/youtube-cookies.txt:ro,Z" \
  lofi-relay
```

The `:ro` flag makes the cookies file read-only inside the container.

The `:Z` flag gives the container the appropriate SELinux label.

Check the container:

```bash
docker ps
```

View logs:

```bash
docker logs -f lofi-relay
```

You should eventually see:

```text
Uvicorn running on http://0.0.0.0:8000
```

---

# Docker health test

```bash
curl http://127.0.0.1:8000/health
```

Expected:

```json
{"status":"ok"}
```

---

# Docker stream test

Load the API key:

```bash
export LOFI_API_KEY="$(grep '^LOFI_API_KEY=' .env | cut -d= -f2-)"
```

Then:

```bash
curl -i \
  -H "X-API-Key: $LOFI_API_KEY" \
  http://127.0.0.1:8000/lofi/relax-study
```

Expected:

```text
HTTP/1.1 200 OK
content-type: audio/aac
```

You can then play it:

```bash
mpv \
  --no-video \
  --really-quiet \
  --http-header-fields="X-API-Key: $LOFI_API_KEY" \
  http://127.0.0.1:8000/lofi/relax-study
```

---

# Docker and YouTube cookies

The cookies file is deliberately **not copied into the Docker image**.

The image only contains the application and its software dependencies.

At runtime:

```text
Host
 │
 ├── .env
 │     └── LOFI_API_KEY
 │
 └── youtube-cookies.txt
       │
       │ read-only mount
       ▼
Docker container
 │
 └── /run/secrets/youtube-cookies.txt
       │
       ▼
    yt-dlp
```

This means rebuilding the image does not require rebuilding it whenever the YouTube cookies are refreshed.

Simply replace the host's:

```text
youtube-cookies.txt
```

and recreate the container.

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
├── .env                 # local secret — NOT committed
└── youtube-cookies.txt  # local secret — NOT committed
```

---

# Architecture

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
Deno / EJS challenge solver
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

Provides the HTTP API and connects the components together.

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

The following files contain secrets and must never be committed:

```text
.env
youtube-cookies.txt
```

Check before committing:

```bash
git status
```

You can also verify that the cookie file is ignored:

```bash
git status --short --ignored youtube-cookies.txt
```

Never put secrets in:

* `config.py`
* `main.py`
* `auth.py`
* `Dockerfile`
* Git
* committed shell scripts
* the Docker image

The YouTube cookies provide access to the authenticated YouTube session from which they were exported. Treat them with the same care as a login session credential.

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

## YouTube says `Sign in to confirm you're not a bot`

The cookies may be missing or invalid.

First test the cookies directly with:

```bash
yt-dlp \
  --cookies youtube-cookies.txt \
  --remote-components ejs:github \
  -F "YOUTUBE_URL"
```

If this works and shows available formats, the cookies and Deno/EJS setup are functioning.

If it fails with authentication-related errors, export a fresh cookies file.

---

## `n challenge solving failed`

Make sure Deno is installed:

```bash
deno --version
```

Then make sure `yt-dlp` can download the EJS challenge solver:

```bash
yt-dlp \
  --cookies youtube-cookies.txt \
  --remote-components ejs:github \
  -F "YOUTUBE_URL"
```

The Docker image installs Deno automatically.

---

## Docker cannot read `youtube-cookies.txt`

On Fedora/SELinux systems, make sure the volume mount uses `:Z`:

```bash
-v "$(pwd)/youtube-cookies.txt:/run/secrets/youtube-cookies.txt:ro,Z"
```

Without the SELinux relabeling, the container may receive:

```text
Permission denied
```

even when the file permissions on the host appear correct.

---

## FFmpeg not found

Check:

```bash
ffmpeg -version
```

For Docker, rebuild the image:

```bash
docker build -t lofi-relay .
```

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
Private Git repository
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
    Deno / EJS
         │
         ▼
      YouTube
```

The production API key should be configured as:

```text
LOFI_API_KEY
```

The YouTube cookies should be provided to the container as a secret or read-only mounted file.

The `.env` file and `youtube-cookies.txt` should **not** be committed to Git or baked into the Docker image.

---

# Development workflow

After making changes:

```bash
git status
```

Review:

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

Before pushing, verify that secrets are not tracked:

```bash
git status
```

---

# Purpose

Lofi Relay exists for one simple purpose:

> **Turn livestreams that expose combined video/audio HLS streams into lightweight audio-only HTTP streams.**

The client only receives the audio it actually needs.

The service is intentionally small and modular. There is no database, user-management system, queue, caching layer, or other infrastructure that isn't necessary for its intended private use.

> **Currently, development is focused on Lo-Fi Girl livestreams. The service should technically work with other YouTube livestreams, but those have not been extensively tested.**
