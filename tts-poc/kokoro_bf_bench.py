import time, numpy as np, soundfile as sf
from mlx_audio.tts.utils import load_model

TEXT = open("test_text.txt").read().strip()
REPO = "mlx-community/Kokoro-82M-4bit"
VOICE = "bf_emma"  # British female, to accent-match Apple Serena (en-GB)

t0 = time.perf_counter()
model = load_model(REPO)
print(f"[load] {time.perf_counter()-t0:.2f}s")

def run(text):
    parts = []
    # lang_code='b' = British English G2P, to match the bf_ British voice
    for r in model.generate(text=text, voice=VOICE, speed=1.0, lang_code="b"):
        parts.append(np.array(r.audio).reshape(-1))
    return np.concatenate(parts).astype(np.float32)

# warm-up
_ = run("Warm up the kernels now.")

t0 = time.perf_counter()
audio = run(TEXT)
synth = time.perf_counter() - t0
sr = 24000
dur = len(audio) / sr
sf.write("samples/kokoro_bf_1x.wav", audio, sr)
print(f"[synth] voice={VOICE} wall={synth:.2f}s dur={dur:.2f}s RTF={synth/dur:.3f} xRT={dur/synth:.1f}")
