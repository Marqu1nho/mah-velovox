import time, soundfile as sf
from f5_tts_mlx.generate import generate

TEXT = open("test_text.txt").read().strip()

# Clone reference: a 7.5s slice of the Apple Serena (en-GB, British female) clip,
# cut at a clean sentence boundary so the transcript matches exactly.
REF_AUDIO = "samples/f5_ref_serena_6s.wav"
REF_TEXT = ("The quarterly report shows revenue up twelve percent. "
            "When the function returns nil, the parser falls back to the default config.")

OUT = "samples/f5_1x.wav"

t0 = time.perf_counter()
generate(
    generation_text=TEXT,
    ref_audio_path=REF_AUDIO,
    ref_audio_text=REF_TEXT,
    model_name="lucasnewman/f5-tts-mlx",
    # Explicit TOTAL duration (ref ~7.5s + ~16.5s of new speech). Neither the default
    # (truncates to ~ref length) nor estimate_duration=True (over-generated to 161s!)
    # gave a usable clip, so we pin it near Kokoro/Apple's ~16.5s of speech.
    duration=24.0,
    steps=8,
    output_path=OUT,
)
synth = time.perf_counter() - t0

info = sf.info(OUT)
dur = info.duration
print(f"[f5 synth] wall={synth:.2f}s dur={dur:.2f}s RTF={synth/dur:.3f} xRT={dur/synth:.1f} sr={info.samplerate}")
