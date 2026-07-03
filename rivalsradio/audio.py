"""System-audio analysis for the Stage visualizer.

Captures what the PC is playing (WASAPI loopback via ``soundcard``) and
exposes:

- ``spectrum()`` — 56 log-spaced band magnitudes in [0, 1], smoothed
  (snappy attack / gentle decay), updated ~187×/s for fluid high-FPS bars.

- ``beat()``    — a kick-drum envelope in [0, 1]. It fires once per bass-drum
  hit and decays (~150 ms), driven by three gates that each encode one rule:

    rise       the low band (35–130 Hz) must jump ≥ ~5 dB within ~45 ms —
               a drum transient. A sustained bass note has no rise → silent.
    dominance  the low band must be within ~6 dB of the rest of the spectrum
               (200 Hz–8 kHz). Snares/hi-hats/vocals leak a little energy into
               the low bins, but they are bass-QUIET relative to their own
               body — sections with only highs/mids stay silent.
    level      the hit must reach a fraction of the track's own recent kick
               level (a decaying PEAK, ~4 s half-life). Because kicks
               themselves set this reference, a busy mix can never "catch up"
               and mute the detector (no stall-and-resume).

  All measurements are absolute dB of the raw FFT — deliberately NOT the
  normalized display spectrum, whose auto-gain makes leakage jitter look like
  signal when real bass is absent.

The DSP lives in ``Analyzer`` (pure, feed-samples-in), so it can be tested by
synthesizing real waveforms offline; ``AudioEngine`` wraps it with the capture
thread. Without a capture backend everything degrades to silence.
"""

from __future__ import annotations

import threading
from typing import Optional, Tuple

import numpy as np

BANDS = 56
SAMPLERATE = 48000
BLOCK = 4096            # FFT window: ~11.7 Hz bins (real low-end resolution)
HOP = 256               # ~187 analysis updates per second

# Kick detector tuning.
RISE_DB = 5.0           # hit must exceed ALL of the pre-attack window by this
HIST_HOPS = 17          # low-band history length (~90 ms)
REF_HOPS = 8            # reference = max of the OLDEST 8 entries (53–90 ms
                        # ago): far enough back to clear the FFT window's
                        # smear of the kick attack, and a max-of-8 so noise
                        # wander almost never beats it by RISE_DB
DOMINANCE_DB = 6.0      # low band may be at most this far below the rest
PEAK_FRAC_DB = 5.5      # hit must be within this of the recent kick peak
PEAK_FALL_DB = 0.004    # peak reference decay per hop (≈3 dB / 4 s)
SILENCE_DB = -60.0      # absolute floor — digital-silence guard
WARMUP_HOPS = 90        # ≈0.5 s before the detector may fire at all
REFRACTORY = 23         # hops (~120 ms) between fires
ENV_DECAY = 0.9755      # per-hop envelope decay (~150 ms half-life)


class Analyzer:
    """Pure per-hop DSP: feed mono float chunks, read spectrum + beat."""

    def __init__(self) -> None:
        self._buf = np.zeros(BLOCK, dtype=np.float32)
        self._window = np.hanning(BLOCK).astype(np.float32)
        self.spectrum = np.zeros(BANDS, dtype=np.float32)
        self.beat = 0.0

        freqs = np.fft.rfftfreq(BLOCK, 1.0 / SAMPLERATE)
        # Display bands (log-spaced, nearest-bin fallback so no bar is dead).
        edges = np.logspace(np.log10(40.0), np.log10(16000.0), BANDS + 1)
        self._bins = []
        for i in range(BANDS):
            idx = np.where((freqs >= edges[i]) & (freqs < edges[i + 1]))[0]
            if idx.size == 0:
                centre = float(np.sqrt(edges[i] * edges[i + 1]))
                idx = np.array([int(np.argmin(np.abs(freqs - centre)))])
            self._bins.append(idx)
        # Kick-detector bands.
        self._low = np.where((freqs >= 35.0) & (freqs <= 130.0))[0]
        self._rest = np.where((freqs >= 200.0) & (freqs <= 8000.0))[0]

        self._disp_peak = 1e-6
        self._hist = np.full(HIST_HOPS, -120.0, dtype=np.float64)
        self._level_peak = -120.0
        self._age = 0
        self._cooldown = 0

    def process(self, mono: np.ndarray) -> None:
        """Advance the analysis by one captured chunk (any length ≥ 1)."""
        m = mono.shape[0]
        if m >= BLOCK:
            self._buf = mono[-BLOCK:].astype(np.float32)
        elif m > 0:
            self._buf = np.roll(self._buf, -m)
            self._buf[-m:] = mono
        mag = np.abs(np.fft.rfft(self._buf * self._window))

        # ---- display spectrum (normalized, smoothed) ---------------------
        raw = np.empty(BANDS, dtype=np.float32)
        for i, idx in enumerate(self._bins):
            raw[i] = mag[idx].mean()
        raw = np.log1p(raw)
        self._disp_peak = max(self._disp_peak * 0.999, float(raw.max()), 1e-6)
        norm = np.clip(raw / self._disp_peak, 0.0, 1.0)
        rise = norm > self.spectrum
        self.spectrum = np.where(
            rise, self.spectrum + (norm - self.spectrum) * 0.28,
            self.spectrum + (norm - self.spectrum) * 0.06).astype(np.float32)

        # ---- kick detection (absolute dB, three gates) --------------------
        low_db = 10.0 * np.log10(float(np.mean(mag[self._low] ** 2)) + 1e-12)
        rest_db = 10.0 * np.log10(float(np.mean(mag[self._rest] ** 2)) + 1e-12)

        ref = float(self._hist[:REF_HOPS].max())   # pre-attack window max
        self._hist = np.roll(self._hist, -1)
        self._hist[-1] = low_db

        if self._age == 0:
            self._level_peak = low_db              # no −120 dB cold-start spike
        self._level_peak = max(self._level_peak - PEAK_FALL_DB, low_db)
        if self._age <= WARMUP_HOPS:
            self._age += 1
        if self._cooldown > 0:
            self._cooldown -= 1

        if (self._cooldown == 0
                and self._age > WARMUP_HOPS
                and low_db - ref >= RISE_DB                      # drum transient
                and low_db >= rest_db - DOMINANCE_DB             # real bass, not leakage
                and low_db >= self._level_peak - PEAK_FRAC_DB    # kick-scale level
                and low_db > SILENCE_DB):
            self.beat = 1.0
            self._cooldown = REFRACTORY
        else:
            self.beat *= ENV_DECAY
            if self.beat < 0.002:
                self.beat = 0.0


class AudioEngine:
    def __init__(self) -> None:
        self._an = Analyzer()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._spectrum = np.zeros(BANDS, dtype=np.float32)
        self._beat = 0.0
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
        try:
            with mic.recorder(samplerate=SAMPLERATE, channels=1,
                              blocksize=HOP) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=HOP)
                    mono = data[:, 0] if data.ndim > 1 else data
                    self._an.process(mono)
                    with self._lock:
                        self._spectrum = self._an.spectrum.copy()
                        self._beat = float(self._an.beat)
        except Exception:
            self.available = False
