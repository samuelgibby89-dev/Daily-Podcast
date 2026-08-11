"""Text-to-speech for the daily brief.

Primary engine is edge-tts: genuinely free, no API key, no quota, and the
neural voices sound like a real person. If Microsoft's endpoint ever refuses
a GitHub runner, we fall back to gTTS so the show still ships.

Set a paid engine later by exporting TTS_ENGINE=openai plus OPENAI_API_KEY.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

DEFAULT_VOICE = os.getenv("TTS_VOICE", "en-US-AndrewMultilingualNeural")
DEFAULT_RATE = os.getenv("TTS_RATE", "+4%")

# A few voices worth trying -- swap via the TTS_VOICE env var / repo variable:
#   en-US-AndrewMultilingualNeural  warm, conversational male  (default)
#   en-US-BrianMultilingualNeural   crisp newsreader male
#   en-US-AvaMultilingualNeural     warm, conversational female
#   en-US-EmmaMultilingualNeural    bright, upbeat female
#   en-GB-RyanNeural                British male


def clean_for_speech(text: str) -> str:
    """Strip anything a narrator would awkwardly read out loud."""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)  # headings
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)                # bold
    text = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text)       # italics
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)  # bullets
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)             # links
    text = text.replace("&", " and ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _edge_tts(text: str, out_path: Path, voice: str, rate: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(str(out_path))


def _gtts(text: str, out_path: Path) -> None:
    from gtts import gTTS  # type: ignore

    gTTS(text=text, lang="en", tld="com").save(str(out_path))


def _openai_tts(text: str, out_path: Path) -> None:
    from openai import OpenAI  # type: ignore

    client = OpenAI()
    voice = os.getenv("OPENAI_TTS_VOICE", "onyx")
    model = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    with client.audio.speech.with_streaming_response.create(
        model=model, voice=voice, input=text
    ) as response:
        response.stream_to_file(str(out_path))


def _compress(path: Path) -> None:
    """Re-encode to small, even-loudness mono speech audio.

    A 7-minute brief lands around 1.5 MB, which keeps the repo tiny and makes
    the episode load instantly on a phone.
    """
    if not shutil.which("ffmpeg"):
        print("  ! ffmpeg not found, shipping raw TTS output")
        return

    tmp = path.with_suffix(".tmp.mp3")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(path),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ac", "1", "-ar", "24000", "-b:a", "32k",
        str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True)
        tmp.replace(path)
    except subprocess.CalledProcessError as exc:
        print(f"  ! ffmpeg pass failed ({exc}); keeping raw audio")
        tmp.unlink(missing_ok=True)


def synthesize(text: str, out_path: Path) -> Path:
    """Turn the episode script into an MP3. Raises only if every engine fails."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    spoken = clean_for_speech(text)
    engine = os.getenv("TTS_ENGINE", "edge").lower()

    attempts: list[tuple[str, object]] = []
    if engine == "openai":
        attempts.append(("openai", lambda: _openai_tts(spoken, out_path)))

    # Microsoft's endpoint hands out occasional 403s. They are almost always
    # transient, so try a few times with backoff before giving up on it.
    for i in range(3):
        attempts.append(
            (f"edge-tts (try {i + 1}/3)", lambda i=i: (
                time.sleep(5 * i),
                asyncio.run(_edge_tts(spoken, out_path, DEFAULT_VOICE, DEFAULT_RATE)),
            ))
        )

    attempts.append(("gTTS (fallback voice)", lambda: _gtts(spoken, out_path)))

    last_error: Exception | None = None
    for name, fn in attempts:
        try:
            print(f"  synthesizing with {name} ...")
            fn()
            if out_path.exists() and out_path.stat().st_size > 10_000:
                _compress(out_path)
                size_mb = out_path.stat().st_size / 1_048_576
                print(f"  + audio written: {out_path.name} ({size_mb:.2f} MB)")
                return out_path
            raise RuntimeError("engine produced an empty or truncated file")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {name} failed: {exc.__class__.__name__}: {exc}")
            last_error = exc
            out_path.unlink(missing_ok=True)

    raise RuntimeError(f"all TTS engines failed; last error: {last_error}")


def duration_seconds(path: Path) -> int:
    """Length of the finished MP3, for the player UI and the RSS feed."""
    try:
        from mutagen.mp3 import MP3

        return int(MP3(str(path)).info.length)
    except Exception:
        pass
    if shutil.which("ffprobe"):
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, check=True,
            )
            return int(float(out.stdout.strip()))
        except Exception:
            pass
    return 0
