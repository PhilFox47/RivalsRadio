"""System-audio-reactive spectrum source for the Stage visualizer.

Captures whatever your PC is playing through the default speaker (WASAPI
loopback on Windows) via the ``soundcard`` library, runs an FFT, and exposes a
smoothed, normalized set of frequency-band magnitudes in [0, 1].

Everything is guarded: if no capture backend is available (e.g. running on a
machine without loopback support), ``available`` is False and ``get_spectrum``
returns zeros, so the UI degrades gracefully instead of crashing.
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np


class AudioVisualizer:
    def __init__(self, bands: int = 56, samplerate: int = 48000,
                 blocksize: int = 2048, hop: int = 256) -> None:
        self.bands = bands
        self.samplerate = samplerate
        self.blocksize = blocksize
        # Capture in small hops but FFT over the full block (rolling buffer) so
        # the spectrum updates ~samplerate/hop times a second (e.g. 48000/256 ≈
        # 187 Hz) instead of once per block (~23 Hz) — smooth, high-FPS bars.
        self.hop = hop

        self._spectrum = np.zeros(bands, dtype=np.float32)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._window = np.hanning(blocksize).astype(np.float32)

        # Pre-compute log-spaced band edges across the audible range.
        freqs = np.fft.rfftfreq(blocksize, 1.0 / samplerate)
        lo, hi = 40.0, min(16000.0, samplerate / 2)
        edges = np.logspace(np.log10(lo), np.log10(hi), bands + 1)
        self._band_idx = [
            np.where((freqs >= edges[i]) & (freqs < edges[i + 1]))[0]
            for i in range(bands)
        ]

        self.available = self._backend_available()
        self._error: Optional[str] = None

    @staticmethod
    def _backend_available() -> bool:
        try:
            import soundcard  # noqa: F401
            return True
        except Exception:
            return False

    @property
    def error(self) -> Optional[str]:
        return self._error

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.available:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="audioviz", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)
        self._thread = None
        with self._lock:
            self._spectrum[:] = 0.0

    def get_spectrum(self) -> np.ndarray:
        with self._lock:
            return self._spectrum.copy()

    # ------------------------------------------------------------------
    def _run(self) -> None:
        try:
            import soundcard as sc
            speaker = sc.default_speaker()
            mic = sc.get_microphone(speaker.name, include_loopback=True)
        except Exception as exc:  # pragma: no cover - hardware dependent
            self._error = f"audio capture unavailable: {exc}"
            self.available = False
            return

        smoothed = np.zeros(self.bands, dtype=np.float32)
        buf = np.zeros(self.blocksize, dtype=np.float32)   # rolling FFT window
        # Running peak for auto-gain so quiet and loud tracks both look good.
        peak = 1e-6
        try:
            with mic.recorder(samplerate=self.samplerate, channels=1,
                              blocksize=self.hop) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=self.hop)
                    mono = data[:, 0] if data.ndim > 1 else data
                    m = mono.shape[0]
                    if m >= self.blocksize:
                        buf = mono[-self.blocksize:].astype(np.float32)
                    elif m > 0:
                        buf = np.roll(buf, -m)
                        buf[-m:] = mono
                    mag = np.abs(np.fft.rfft(buf * self._window))

                    raw = np.empty(self.bands, dtype=np.float32)
                    for i, idx in enumerate(self._band_idx):
                        raw[i] = mag[idx].mean() if idx.size else 0.0

                    # Log compression for a more musical response.
                    raw = np.log1p(raw)
                    peak = max(peak * 0.999, float(raw.max()), 1e-6)
                    norm = np.clip(raw / peak, 0.0, 1.0)

                    # Asymmetric smoothing: snappy attack, gentle decay. Factors
                    # are tuned for the high (~hop) update rate so it stays fluid
                    # rather than twitchy.
                    rise = norm > smoothed
                    smoothed = np.where(
                        rise,
                        smoothed + (norm - smoothed) * 0.28,
                        smoothed + (norm - smoothed) * 0.06,
                    ).astype(np.float32)

                    with self._lock:
                        self._spectrum = smoothed.copy()
        except Exception as exc:  # pragma: no cover - hardware dependent
            self._error = f"audio capture stopped: {exc}"
            self.available = False
