"""Speech-to-text for uploaded videos, so the projector can run them muted.

Nobody wants a reception's PA fighting a laptop's audio, but a silent video of
someone giving a toast is just a person moving their mouth. Transcribing it
locally gets the words on screen without sending anyone's wedding to a cloud
API.

Everything here degrades to "no captions" rather than failing an upload:
faster-whisper is optional, and a video with no speech simply gets none.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
from pathlib import Path

from .config import settings

_model = None
_model_lock = threading.Lock()


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return settings.transcribe_video


def _get_model():
    """Load once and keep it. Loading costs a few seconds; inference doesn't."""
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = WhisperModel(
                settings.whisper_model, device="cpu", compute_type="int8"
            )
        return _model


def has_audio(path: Path) -> bool:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return "audio" in (proc.stdout or "")
    except Exception:
        return False


def transcribe(path: Path) -> list[dict]:
    """Return [{start, end, text}, ...]. Empty when there's nothing to say."""
    if not available() or not has_audio(path):
        return []

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        # 16 kHz mono PCM is what the model expects; anything else it resamples
        # internally anyway, and this keeps the temp file small.
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(path),
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
            capture_output=True, timeout=900,
        )
        if proc.returncode != 0 or not wav.exists():
            return []

        model = _get_model()
        segments, _info = model.transcribe(
            str(wav),
            language=settings.whisper_language or None,
            vad_filter=True,                       # skips music and room noise
            vad_parameters=dict(min_silence_duration_ms=400),
            word_timestamps=True,                  # needed to cut readable cues
        )
        return _to_cues(segments)


# Subtitle cues, not transcript segments. Whisper happily returns a single
# seven-second block of text; on a projector that's a wall of words that
# appears and vanishes. These get cut to something you can actually read at a
# glance, preferring sentence ends, then commas, then length.
MAX_CUE_SECONDS = 3.6
MAX_CUE_CHARS = 46


def _to_cues(segments) -> list[dict]:
    cues: list[dict] = []
    words: list = []
    for seg in segments:
        words.extend(getattr(seg, "words", None) or [])

    if not words:
        # No word timings (older model or odd audio) — fall back to segments.
        for seg in segments:
            text = (seg.text or "").strip()
            if text:
                cues.append({"start": round(seg.start, 2),
                             "end": round(seg.end, 2), "text": text[:300]})
        return cues[:400]

    buf: list = []

    def flush() -> None:
        if not buf:
            return
        text = "".join(w.word for w in buf).strip()
        if text:
            cues.append({"start": round(buf[0].start, 2),
                         "end": round(buf[-1].end, 2), "text": text})
        buf.clear()

    for word in words:
        buf.append(word)
        text = "".join(w.word for w in buf).strip()
        span = buf[-1].end - buf[0].start
        ends_sentence = text.endswith((".", "!", "?", "…"))
        ends_clause = text.endswith((",", ";", ":", "—"))

        # A finished sentence is the best place to cut. Otherwise cut at a
        # clause end once we're close to the budget, and force a cut at it.
        near_budget = span >= MAX_CUE_SECONDS * 0.7 or len(text) >= MAX_CUE_CHARS * 0.7
        over_budget = span >= MAX_CUE_SECONDS or len(text) >= MAX_CUE_CHARS

        if (ends_sentence and len(text) >= 12) or (ends_clause and near_budget) or over_budget:
            flush()
        if len(cues) >= 400:
            break
    flush()
    return cues[:400]


def to_json(segments: list[dict]) -> str | None:
    return json.dumps(segments, ensure_ascii=False) if segments else None


def from_json(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []
