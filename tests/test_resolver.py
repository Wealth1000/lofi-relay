"""Seam 1 for the resolver: stub yt-dlp and assert on the published document.

The seam is the highest one available: a scripted ``yt_dlp`` module stands in
for the entire YouTube boundary, and the assertions are on the document the
resolver publishes -- entry shape, merge-preservation of last-good URLs,
status/exit-code transitions. Nothing about yt-dlp internals is tested.
"""

import json
import sys
import time
import types

import pytest
from yt_dlp.utils import DownloadError

import tools.resolve_streams as rs
from app.config import RATE_LIMIT_COOLDOWN, STREAMS

ALL_SOURCES = list(STREAMS.values())


def _script_ok(url: str, idx: int) -> list:
    return [
        (
            "ok",
            {
                "url": f"https://manifest.googlevideo.com/fresh/{idx}?id={url[-11:]}",
                "vcodec": "none",
                "format_id": "234",
            },
        )
    ]


def _prev_document(tmp_path) -> object:
    """A previous document where every stream has a known-good URL."""
    document = {
        "version": 1,
        "updated_at": "2026-10-12T00:00:00+00:00",
        "status": "ok",
        "last_error": None,
        "streams": {
            name: {
                "source": source,
                "video_id": rs.extract_video_id(source),
                "label": rs.STREAM_LABELS.get(name),
                "url": f"https://prev.example/{name}",
                "variant": "audio-only",
                "format_id": "234",
                "resolved_at": "2026-10-12T00:00:00+00:00",
                "stale": False,
                "error": None,
            }
            for name, source in STREAMS.items()
        },
    }
    path = tmp_path / "streams.json"
    path.write_text(json.dumps(document))
    return path


@pytest.fixture
def empty_previous(tmp_path) -> object:
    path = tmp_path / "streams.json"
    path.write_text(json.dumps({"streams": {}}))
    return path


@pytest.fixture
def fake(monkeypatch):
    """Install a scripted yt_dlp stand-in; returns a dict to fill with scripts.

    scripts maps source URL -> a list of ("kind", payload) attempts. The list
    is consumed left to right (one entry per client-strategy attempt); when it
    has one entry it is repeated. Kinds: ok / fail / rate / login.
    """
    monkeypatch.setattr(rs, "_seed_cookie_file", lambda: None)

    class _State:
        scripts: dict = {}

    state = _State()

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def extract_info(self, url, download=False):
            queue = state.scripts.get(url)
            assert queue is not None, f"no script for {url}"
            kind, payload = queue.pop(0) if len(queue) > 1 else queue[0]

            if kind == "ok":
                return {
                    "url": payload["url"],
                    "vcodec": payload.get("vcodec", "none"),
                    "format_id": payload.get("format_id", "234"),
                }

            if kind == "rate":
                raise DownloadError("HTTP error 429: Too Many Requests")
            if kind == "login":
                raise DownloadError("Sign in to confirm you're not a bot")

            raise DownloadError(payload)

    monkeypatch.setattr(rs, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    return state


def _fill(state, scripts_by_source):
    state.scripts = scripts_by_source


def _run(path, empty_previous_doc):
    return rs.run(str(path))


def test_all_resolved_publishes_fresh_document(tmp_path, fake):
    _fill(
        fake,
        {
            source: _script_ok(source, i)
            for i, source in enumerate(ALL_SOURCES)
        },
    )
    out = _prev_document(tmp_path)

    assert _run(out, False) == 0

    document = json.loads(out.read_text())
    assert document["status"] == "ok"
    assert document["last_error"] is None
    assert document["version"] == 1

    for i, name in enumerate(STREAMS):
        entry = document["streams"][name]
        assert entry["stale"] is False
        assert entry["error"] is None
        assert f"fresh/{i}" in entry["url"]
        assert entry["variant"] == "audio-only"
        assert entry["format_id"] == "234"
        assert entry["resolved_at"].startswith("2026-")
        assert entry["source"] == STREAMS[name]
        assert entry["label"] == rs.STREAM_LABELS[name]

    # live-URL shapes carry their video ID through
    assert document["streams"]["asian-relax-study"]["video_id"] == "1Tl2FtV06qo"
    assert document["streams"]["relax-study"]["video_id"] == "rFZHOHl-L8A"


def test_failed_stream_preserves_last_good(tmp_path, fake):
    scripts = {source: _script_ok(source, i) for i, source in enumerate(ALL_SOURCES)}
    scripts[STREAMS["christmas-lofi"]] = [("fail", "Video unavailable")]
    _fill(fake, scripts)
    out = _prev_document(tmp_path)

    assert _run(out, False) == 0

    document = json.loads(out.read_text())
    entry = document["streams"]["christmas-lofi"]
    assert entry["stale"] is True
    assert entry["url"] == "https://prev.example/christmas-lofi"
    assert entry["error"] == "Video unavailable"
    assert document["status"] == "ok"

    other = document["streams"]["relax-study"]
    assert other["stale"] is False
    assert "fresh/" in other["url"]


def test_session_rejection_defers_rest_and_preserves(tmp_path, fake):
    scripts = {
        source: _script_ok(source, i) for i, source in enumerate(ALL_SOURCES)
    }
    first = ALL_SOURCES[0]
    scripts[first] = [("login", "Sign in to confirm you're not a bot")]
    _fill(fake, scripts)
    out = _prev_document(tmp_path)

    assert _run(out, False) == 0

    document = json.loads(out.read_text())
    assert "rejected" in document["last_error"]

    for name, entry in document["streams"].items():
        assert entry["stale"] is True, name
        assert entry["url"] == f"https://prev.example/{name}"
        assert "deferred" in entry["error"] or "rejected" in entry["error"]


def test_session_rejection_with_no_previous_is_total_failure(empty_previous, fake):
    out = empty_previous
    scripts = {source: _script_ok(source, i) for i, source in enumerate(ALL_SOURCES)}
    scripts[ALL_SOURCES[0]] = [("login", "Sign in to confirm you're not a bot")]
    _fill(fake, scripts)

    assert _run(out, False) == 1

    document = json.loads(out.read_text())
    assert document["status"] == "stale"
    assert all(e["url"] is None for e in document["streams"].values())


def test_rate_limit_cools_down_once_then_defers(tmp_path, fake, monkeypatch):
    sleeps: list = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

    scripts = {source: _script_ok(source, i) for i, source in enumerate(ALL_SOURCES)}
    scripts[ALL_SOURCES[0]] = [
        ("rate", "HTTP error 429: Too Many Requests"),
        ("rate", "HTTP error 429: Too Many Requests"),
    ]
    _fill(fake, scripts)
    out = _prev_document(tmp_path)

    assert _run(out, False) == 0
    assert sleeps == [RATE_LIMIT_COOLDOWN], "cooldown must happen exactly once per run"

    document = json.loads(out.read_text())
    first_name = next(name for name, src in STREAMS.items() if src == ALL_SOURCES[0])
    assert "rate limited" in document["streams"][first_name]["error"]
    for name, entry in document["streams"].items():
        assert entry["stale"] is True


def test_rate_limit_recovers_after_cooldown(tmp_path, fake, monkeypatch):
    sleeps: list = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))

    scripts = {source: _script_ok(source, i) for i, source in enumerate(ALL_SOURCES)}
    scripts[ALL_SOURCES[0]] = [
        ("rate", "HTTP error 429: Too Many Requests"),
        ("ok", {"url": "https://manifest.googlevideo.com/recovered", "vcodec": "none", "format_id": "234"}),
    ]
    _fill(fake, scripts)
    out = _prev_document(tmp_path)

    assert _run(out, False) == 0
    assert len(sleeps) == 1

    document = json.loads(out.read_text())
    entry = document["streams"]["lofi-house"]
    assert entry["stale"] is False
    assert entry["url"] == "https://manifest.googlevideo.com/recovered"
    assert all(not e["stale"] for e in document["streams"].values())


def test_muxed_fallback_is_flagged(tmp_path, fake):
    scripts = {source: _script_ok(source, i) for i, source in enumerate(ALL_SOURCES)}
    scripts[ALL_SOURCES[0]] = [
        (
            "ok",
            {"url": "https://manifest.googlevideo.com/muxed", "vcodec": "avc1.4d401e", "format_id": "96"},
        )
    ]
    _fill(fake, scripts)
    out = _prev_document(tmp_path)

    assert _run(out, False) == 0

    document = json.loads(out.read_text())
    assert document["streams"]["lofi-house"]["variant"] == "muxed-fallback"


def test_malformed_previous_starts_fresh(tmp_path, fake):
    out = tmp_path / "streams.json"
    out.write_text('{"streams": "not-a-dict"}')
    scripts = {source: _script_ok(source, i) for i, source in enumerate(ALL_SOURCES)}
    _fill(fake, scripts)

    assert _run(out, False) == 0
    document = json.loads(out.read_text())
    assert document["status"] == "ok"
    assert len(document["streams"]) == len(STREAMS)


def test_main_dry_run_prints_instead_of_writing(tmp_path, fake, capsys):
    out = tmp_path / "nope.json"
    scripts = {source: _script_ok(source, i) for i, source in enumerate(ALL_SOURCES)}
    _fill(fake, scripts)

    code = rs.main(["--document", str(out), "--dry-run"])

    assert code == 0
    assert not out.exists()
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "ok"
    assert len(printed["streams"]) == len(STREAMS)
