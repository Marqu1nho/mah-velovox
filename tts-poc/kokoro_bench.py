import time, numpy as np, soundfile as sf
from mlx_audio.tts.utils import load_model

TEXT = ("The quarterly report shows revenue up twelve percent. When the function returns nil, "
        "the parser falls back to the default config. Honestly, the hardest part was getting "
        "the streaming to feel instant. She said it would take about three weeks. "
        "Let's revisit the architecture after the demo.")
REPO = "mlx-community/Kokoro-82M-4bit"

t0=time.perf_counter()
model = load_model(REPO)
print(f"[load] {time.perf_counter()-t0:.2f}s")

def run(text):
    parts=[]
    for r in model.generate(text=text, voice="af_heart", speed=1.0):
        parts.append(np.array(r.audio).reshape(-1))
    return np.concatenate(parts).astype(np.float32)

# warm-up
_=run("Warm up the kernels now.")

t0=time.perf_counter()
audio=run(TEXT)
synth=time.perf_counter()-t0
sr=24000; dur=len(audio)/sr
sf.write("samples/kokoro_1x.wav", audio, sr)
print(f"[synth] wall={synth:.2f}s dur={dur:.2f}s RTF={synth/dur:.3f} xRT={dur/synth:.1f}")
