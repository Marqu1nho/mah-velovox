import time
from f5_tts_mlx.generate import generate
TEXT = open("test_text.txt").read().strip()
REF_TEXT = ("The quarterly report shows revenue up twelve percent. "
            "When the function returns nil, the parser falls back to the default config.")
generate(generation_text=TEXT, ref_audio_path="samples/premium_ref.wav", ref_audio_text=REF_TEXT,
         model_name="lucasnewman/f5-tts-mlx", duration=24.0, steps=8, output_path="samples/f5_premium_1x.wav")
