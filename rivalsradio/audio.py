"""System-audio analysis for the Stage visualizer.

Captures what the PC is playing (WASAPI loopback via ``soundcard``), runs a
rolling FFT and exposes:

- ``spectrum()`` — 56 log-spaced band magnitudes in [0, 1], smoothed
  (snappy attack / gentle decay), updated ~187×/s for fluid high-FPS bars.
- ``beat()``    — a kick-drum envelope in [0, 1]. A real onset detector
  (adaptive threshold over bass flux + a refractory period) fires exactly once
  per kick and drives a snappy attack / ~190 ms decay envelope, so visuals
  lock to the beat instead of twitching with noise.

Degrades gracefully: without a capture backend, both return silence.
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np

BANDS = 56
SAMPLERATE = 48000
BLOCK = 4096            # FFT window: ~11.7 Hz bins (real low-end resolution)
HOP = 256               # capture hop: ~187 spectrum updates per second


class AudioEngine:
    def __init__(self) -> None:
        self._spectrum = np.zeros(BANDS, dtype=np.float32)
        self._beat = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._window = np.hanning(BLOCK).astype(np.float32)

        # Log-spaced band → FFT-bin mapping. Low bands can be narrower than a
        # bin; fall back to the nearest bin so no bar is ever dead.
        freqs = np.fft.rfftfreq(BLOCK, 1.0 / SAMPLERATE)
        edges = np.logspace(np.log10(40.0), np.log10(16000.0), BANDS + 1)
        self._bins = []
        for i in range(BANDS):
            idx = np.where((freqs >= edges[i]) & (freqs < edges[i + 1]))[0]
            if idx.size == 0:
                centre = float(np.sqrt(edges[i] * edges[i + 1]))
                idx = np.array([int(np.argmin(np.abs(freqs - centre)))])
            self._bins.append(idx)
        self._bass_n = max(2, BANDS // 6)

        try:
            import soundcard  # noqa: F401
            self.available = True
        except Exception:
            self.available = False

    # ------------------------------------------------------------------
    def start(self) -> None:
        if not self.available or (self._thread and self._thread.is_alive()):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="audio", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)
        self._thread = None

    def spectrum(self) -> np.ndarray:
        with self._lock:
            return self._spectrum.copy()

    def beat(self) -> float:
        with self._lock:
            return self._beat

    # ------------------------------------------------------------------
    def _run(self) -> None:  # pragma: no cover - hardware dependent
        try:
            import soundcard as sc
            speaker = sc.default_speaker()
            mic = sc.get_microphone(speaker.name, include_loopback=True)
        except Exception:
            self.available = False
            return

        smoothed = np.zeros(BANDS, dtype=np.float32)
        buf = np.zeros(BLOCK, dtype=np.float32)
        peak = 1e-6
        bass_prev = -1.0
        flux_avg = 0.02          # adaptive flux baseline (kicks excluded)
        flux_dev = 0.01          # adaptive flux deviation
        bass_slow = -1.0         # slow bass level baseline (~1.3s memory)
        cooldown = 0             # refractory hops after a detected kick
        beat_env = 0.0
        try:
            with mic.recorder(samplerate=SAMPLERATE, channels=1,
                              blocksize=HOP) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=HOP)
                    mono = data[:, 0] if data.ndim > 1 else data
                    m = mono.shape[0]
                    if m >= BLOCK:
                        buf = mono[-BLOCK:].astype(np.float32)
                    elif m > 0:
                        buf = np.roll(buf, -m)
                        buf[-m:] = mono
                    mag = np.abs(np.fft.rfft(buf * self._window))

                    raw = np.empty(BANDS, dtype=np.float32)
                    for i, idx in enumerate(self._bins):
                        raw[i] = mag[idx].mean()
                    raw = np.log1p(raw)
                    peak = max(peak * 0.999, float(raw.max()), 1e-6)
                    norm = np.clip(raw / peak, 0.0, 1.0)

                    rise = norm > smoothed
                    smoothed = np.where(
                        rise, smoothed + (norm - smoothed) * 0.28,
                        smoothed + (norm - smoothed) * 0.06).astype(np.float32)

                    # Kick detection — an onset DETECTOR, not a flux
                    # passthrough. A hit must (a) rise faster than an adaptive
                    # threshold learned from non-kick flux, and (b) lift the
                    # bass LEVEL clearly above its slow baseline (rejects
                    # noise jitter and slow bass swells alike). Each detection
                    # fires once (~120 ms refractory) and snaps the envelope
                    # to 1.0; it then decays with a ~150 ms half-life — a
                    # clean, musical thump per bass-drum hit.
                    bass = float(norm[:self._bass_n].mean())
                    if bass_prev < 0.0:
                        bass_prev = bass
                        bass_slow = bass
                    flux = max(0.0, bass - bass_prev)
                    bass_prev = bass
                    if cooldown > 0:
                        cooldown -= 1
                    thr = max(0.045, flux_avg * 5 + 4 * flux_dev)
                    if flux > thr and bass > bass_slow + 0.05 and cooldown == 0:
                        beat_env = 1.0
                        cooldown = 23              # ≈120 ms at the hop rate
                    else:
                        beat_env *= 0.9755
                        if beat_env < 0.002:
                            beat_env = 0.0
                    if flux <= thr:                # don't learn from kicks
                        flux_avg += (flux - flux_avg) * 0.01
                        flux_dev += (abs(flux - flux_avg) - flux_dev) * 0.01
                    bass_slow += (bass - bass_slow) * 0.004

                    with self._lock:
                        self._spectrum = smoothed.copy()
                        self._beat = float(beat_env)
        except Exception:
            self.available = False
