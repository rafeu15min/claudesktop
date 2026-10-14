"""Is sound actually coming out? Records a few seconds from the default sink's monitor
(what the speakers get) via `parec` and reports peak/RMS in dBFS, plus which apps have
an active stream. The agent can't hear — this turns "does the game play audio" into a
number. Read-only: nothing is played, routed or changed.
"""

from __future__ import annotations

import array
import json
import math
import subprocess

RATE = 48000
CHANNELS = 2
SILENCE_DBFS = -90.0


class AudioError(RuntimeError):
    pass


def _dbfs(value: float) -> float:
    return round(20 * math.log10(value / 32768), 1) if value > 0 else SILENCE_DBFS


def _streams() -> list[dict]:
    try:
        raw = subprocess.run(
            ["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True, text=True, timeout=5, check=True
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise AudioError(f"pactl list sink-inputs failed: {e}") from e
    out = []
    for s in json.loads(raw or "[]"):
        if s.get("client") is None:
            continue  # module-owned plumbing (loopback/remap/combine sinks), not an app
        props = s.get("properties", {})
        out.append({
            "app": props.get("application.name") or props.get("media.name"),
            "media": props.get("media.name"),
            "muted": s.get("mute"),
            "paused": s.get("corked"),
            "volume": next(iter(s.get("volume", {}).values()), {}).get("value_percent"),
        })
    return out


def level(seconds: float = 2.0) -> dict:
    n_bytes = int(seconds * RATE) * CHANNELS * 2
    try:
        proc = subprocess.Popen(
            ["parec", "-d", "@DEFAULT_MONITOR@", "--raw", "--format=s16le", f"--rate={RATE}", f"--channels={CHANNELS}",
             # Default buffering delays the first byte by ~2s (measured 2026-10-14), which
             # shifted the recording window; 50ms makes `seconds` match wall-clock time.
             "--latency-msec=50"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise AudioError("parec not found (needs pipewire-pulse / libpulse)") from e
    try:
        streams = _streams()
        data = proc.stdout.read(n_bytes)
        # Union of start and end, so a short sound that starts mid-recording still shows up.
        streams += [s for s in _streams() if s not in streams]
    finally:
        proc.kill()
        proc.wait()
    if not data:
        raise AudioError(f"parec returned no audio: {proc.stderr.read().decode(errors='replace').strip()}")

    samples = array.array("h", data[: len(data) - len(data) % 2])
    peak = max(abs(v) for v in samples)
    rms = math.sqrt(sum(v * v for v in samples) / len(samples))
    sink = subprocess.run(["pactl", "get-default-sink"], capture_output=True, text=True).stdout.strip()
    return {
        "seconds": round(len(samples) / CHANNELS / RATE, 2),
        "peak_dbfs": _dbfs(peak),
        "rms_dbfs": _dbfs(rms),
        # Rough guide, not a hard rule: under ~-70 dBFS peak is effectively silence.
        "sound_detected": _dbfs(peak) > -70,
        "default_sink": sink,
        "streams": streams,
    }
