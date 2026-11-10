"""Soft effects for the brag video, mixed under the music: a low thump (collapse), a click (pill), a swish (close).
Writes sfx.wav (32 kHz mono, 21 s) with each effect at its cue."""
import wave
import numpy as np

R, DUR = 32000, 21.0
out = np.zeros(int(R * DUR))
rng = np.random.default_rng(3)

def put(at, x):
    i = int(at * R)
    out[i:i + len(x)] += x[:len(out) - i]

def env(n, attack, decay):
    t = np.arange(n) / R
    return np.minimum(1, t / attack) * np.exp(-t / decay)

# thump: a low sine falling 70 -> 48 Hz, round and short
n = int(0.6 * R); t = np.arange(n) / R
put(5.92, 0.55 * np.sin(2 * np.pi * (70 - 22 * t / 0.6) * t) * env(n, 0.004, 0.16))
# click: a tiny filtered noise tick plus a high soft sine
n = int(0.06 * R); t = np.arange(n) / R
noise = np.convolve(rng.standard_normal(n), np.ones(6) / 6, "same")
put(9.27, 0.25 * noise * env(n, 0.0005, 0.008) + 0.12 * np.sin(2 * np.pi * 1760 * t) * env(n, 0.001, 0.012))
# swish: noise through a sweeping low-pass, swelling then gone
n = int(0.55 * R); t = np.arange(n) / R
x = rng.standard_normal(n); y = np.zeros(n); a = 0.0
for i in range(n):
    c = 0.02 + 0.25 * (1 - abs(2 * t[i] / 0.55 - 1))      # cutoff rises then falls
    a += c * (x[i] - a); y[i] = a
put(12.0, 0.35 * y * np.sin(np.pi * t / 0.55) ** 2)

out = np.clip(out, -1, 1)
with wave.open("sfx.wav", "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(R)
    w.writeframes((out * 32767).astype(np.int16).tobytes())
print("sfx.wav", len(out) / R, "s")
