"""Generate the brag video's music with MusicGen-small (local, shared model cache). Writes a 32 kHz mono WAV."""
import sys, wave
import numpy as np
import torch
from transformers import AutoProcessor, MusicgenForConditionalGeneration

out, seconds = sys.argv[1], float(sys.argv[2])
prompts = sys.argv[3:]
torch.manual_seed(7)
torch.set_num_threads(32)
proc = AutoProcessor.from_pretrained("facebook/musicgen-small")
model = MusicgenForConditionalGeneration.from_pretrained("facebook/musicgen-small")
inputs = proc(text=prompts, padding=True, return_tensors="pt")
tokens = int(seconds * 50) + 10                     # ~50 audio tokens per second
with torch.no_grad():
    audio = model.generate(**inputs, max_new_tokens=tokens, do_sample=True, guidance_scale=3.0)
rate = model.config.audio_encoder.sampling_rate
for i, a in enumerate(audio):
    x = a[0].cpu().numpy()
    x = x / max(1e-6, np.abs(x).max()) * 0.89
    pcm = (x * 32767).astype(np.int16)
    path = out if len(prompts) == 1 else out.replace(".wav", f"-{i}.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm.tobytes())
    print(path, len(pcm) / rate, "s")
