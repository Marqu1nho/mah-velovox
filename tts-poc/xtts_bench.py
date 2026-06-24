import time, torch, soundfile as sf
from TTS.api import TTS

TEXT = open("test_text.txt").read().strip()
REF = "samples/f5_ref_serena_6s.wav"  # British female reference (Serena slice), same as F5
OUT = "samples/xtts_1x.wav"

# XTTS-v2 has a known MPS bug on Apple Silicon (coqui-ai/TTS #3649); use CPU to be safe.
device = "cpu"
t0 = time.perf_counter()
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
print(f"[load] {time.perf_counter()-t0:.2f}s device={device}")

t0 = time.perf_counter()
tts.tts_to_file(text=TEXT, speaker_wav=REF, language="en", file_path=OUT)
synth = time.perf_counter() - t0
dur = sf.info(OUT).duration
print(f"[xtts synth] wall={synth:.2f}s dur={dur:.2f}s RTF={synth/dur:.3f} xRT={dur/synth:.2f}")
