"""The CLAP sound model run with ONNX Runtime: turns sounds and descriptions into comparable embeddings.

The model files are kept in one folder: clap_audio.onnx, clap_text.onnx, tokenizer.json and clap_mel_filters.npy.
"""

from pathlib import Path

import numpy as np

MODEL_RATE = 48000
PIECE_SAMPLES = 10 * MODEL_RATE
FFT_SIZE = 1024
HOP = 480
PAD_TOKEN_ID = 1
MAX_TOKENS = 512


def repeat_pad(samples):
	"""Repeat a piece shorter than 10 seconds to fill as much of 10 seconds as it can, then pad with silence."""
	samples = samples[:PIECE_SAMPLES]
	if 0 < len(samples) < PIECE_SAMPLES:
		samples = np.tile(samples, PIECE_SAMPLES // len(samples))
	return np.pad(samples, (0, PIECE_SAMPLES - len(samples)))


class ClapModel:
	"""The CLAP model's audio and text halves, run on the processor with ONNX Runtime."""

	def __init__(self, folder):
		"""Load both halves, the tokenizer and the mel filters from the model folder."""
		import onnxruntime
		from tokenizers import Tokenizer
		folder = Path(folder)
		options = onnxruntime.SessionOptions()
		options.log_severity_level = 3
		providers = ["CPUExecutionProvider"]
		self.audio = onnxruntime.InferenceSession(str(folder / "clap_audio.onnx"), options, providers=providers)
		self.text = onnxruntime.InferenceSession(str(folder / "clap_text.onnx"), options, providers=providers)
		self.tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
		self.tokenizer.enable_padding(pad_id=PAD_TOKEN_ID, pad_token="<pad>")
		self.tokenizer.enable_truncation(MAX_TOKENS)
		self.mel_filters = np.load(folder / "clap_mel_filters.npy")
		self.window = np.hanning(FFT_SIZE + 1)[:-1]

	def mel_features(self, samples):
		"""The log-mel spectrogram of one 10-second piece, as the model expects it: 1001 frames of 64 bands."""
		padded = np.pad(samples.astype(np.float64), FFT_SIZE // 2, mode="reflect")
		frame_count = 1 + (len(padded) - FFT_SIZE) // HOP
		frames = np.lib.stride_tricks.sliding_window_view(padded, FFT_SIZE)[::HOP][:frame_count]
		spectrum = np.fft.rfft(frames * self.window, axis=1).astype(np.complex64)
		power = np.abs(spectrum, dtype=np.float64) ** 2
		mel = np.maximum(1e-10, power @ self.mel_filters)
		return (10.0 * np.log10(mel)).astype(np.float32)

	def embed_audio(self, pieces):
		"""Embed a list of mono 48 kHz pieces, each up to 10 seconds, as unit-length rows."""
		features = np.stack([self.mel_features(repeat_pad(piece))[None, :, :] for piece in pieces])
		return self.audio.run(["embedding"], {"input_features": features})[0]

	def embed_text(self, texts):
		"""Embed a list of descriptions as unit-length rows."""
		encodings = self.tokenizer.encode_batch(list(texts))
		ids = np.array([encoding.ids for encoding in encodings], dtype=np.int64)
		mask = np.array([encoding.attention_mask for encoding in encodings], dtype=np.int64)
		return self.text.run(["embedding"], {"input_ids": ids, "attention_mask": mask})[0]
