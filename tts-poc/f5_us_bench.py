import time, soundfile as sf
from f5_tts_mlx.generate import generate
TEXT = open("test_text.txt").read().strip()
REF_AUDIO = "samples/f5_ref_samantha_us.wav"   # Apple Samantha, en-US → US accent clone
REF_TEXT = ("The quarterly report shows revenue up twelve percent. "
            "When the function returns nil, the parser falls back to the default config.")
OUT = "samples/f5_us_1x.wav"
t0 = time.perf_counter()
generate(generation_text=TEXT, ref_audio_path=REF_AUDIO, ref_audio_text=REF_TEXT,
         model_name="lucasnewman/f5-tts-mlx", duration=24.0, steps=8, output_path=OUT)
synth = time.perf_counter() - t0
info = sf.info(OUT)
print(f"[f5-us] wall={synth:.1f}s dur={info.duration:.2f}s xRT={info.duration/synth:.2f} sr={info.samplerate}")
