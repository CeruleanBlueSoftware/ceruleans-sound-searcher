"""Make the sound model files the app downloads: CLAP's audio and text halves as ONNX, its tokenizer and mel filters.

Only needed to make a new set of model files; the app itself doesn't use this. It needs the packages in
tools/requirements-tools.txt. Usage:

	python tools/make_model_files.py "output folder"

Afterwards, print each file's size and SHA-256 with --checksums and put them in model_download.py.
"""

import hashlib
import sys
from pathlib import Path

MODEL = "laion/larger_clap_general"
FILES = ("MODEL_LICENSE.txt", "tokenizer.json", "clap_mel_filters.npy", "clap_audio.onnx", "clap_text.onnx")


def print_checksums(folder):
	"""Print each model file's size and SHA-256, ready to paste into model_download.py."""
	for name in FILES:
		path = folder / name
		digest = hashlib.sha256()
		with open(path, "rb") as handle:
			while chunk := handle.read(1 << 20):
				digest.update(chunk)
		print(f'\t"{name}": ({path.stat().st_size}, "{digest.hexdigest()}"),')


def make_files(folder):
	"""Convert the model and write every file the app downloads into folder."""
	import numpy as np
	import torch
	from transformers import ClapModel, ClapProcessor

	folder.mkdir(parents=True, exist_ok=True)
	model = ClapModel.from_pretrained(MODEL).eval()
	processor = ClapProcessor.from_pretrained(MODEL)

	def unit(output):
		"""The embedding tensor from a CLAP features call, made unit length."""
		if not torch.is_tensor(output):
			output = output.pooler_output
		return torch.nn.functional.normalize(output, dim=-1)

	class AudioHalf(torch.nn.Module):
		"""Mel features in, unit-length audio embeddings out."""

		def __init__(self):
			"""Wrap the model's audio half."""
			super().__init__()
			self.clap = model

		def forward(self, input_features):
			"""Embed a batch of mel features."""
			return unit(self.clap.get_audio_features(input_features=input_features))

	class TextHalf(torch.nn.Module):
		"""Token IDs and mask in, unit-length text embeddings out."""

		def __init__(self):
			"""Wrap the model's text half."""
			super().__init__()
			self.clap = model

		def forward(self, input_ids, attention_mask):
			"""Embed a batch of tokenized descriptions."""
			return unit(self.clap.get_text_features(input_ids=input_ids, attention_mask=attention_mask))

	audio = processor(audio=[np.zeros(48000 * 3, np.float32), np.zeros(48000 * 10, np.float32)], sampling_rate=48000, return_tensors="pt")
	text = processor(text=["a dog barking", "rain on a tin roof at night"], return_tensors="pt", padding=True)
	torch.onnx.export(
		AudioHalf(), (audio["input_features"],), str(folder / "clap_audio.onnx"), input_names=["input_features"],
		output_names=["embedding"], dynamic_axes={"input_features": {0: "batch"}, "embedding": {0: "batch"}},
		opset_version=17, dynamo=False,
	)
	torch.onnx.export(
		TextHalf(), (text["input_ids"], text["attention_mask"]), str(folder / "clap_text.onnx"),
		input_names=["input_ids", "attention_mask"], output_names=["embedding"],
		dynamic_axes={"input_ids": {0: "batch", 1: "tokens"}, "attention_mask": {0: "batch", 1: "tokens"}, "embedding": {0: "batch"}},
		opset_version=17, dynamo=False,
	)
	processor.tokenizer.backend_tokenizer.save(str(folder / "tokenizer.json"))
	np.save(folder / "clap_mel_filters.npy", processor.feature_extractor.mel_filters_slaney.astype(np.float64))
	licence = Path(sys.argv[0]).resolve().parent / "MODEL_LICENSE.txt"
	(folder / "MODEL_LICENSE.txt").write_bytes(licence.read_bytes())


if __name__ == "__main__":
	output = Path(sys.argv[1])
	if "--checksums" not in sys.argv:
		make_files(output)
	print_checksums(output)
