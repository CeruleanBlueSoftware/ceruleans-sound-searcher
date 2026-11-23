"""Downloads the sound model files once, from the app's GitHub release, checking each one."""

import hashlib
import os
import urllib.request
from pathlib import Path

RELEASE_URL = "https://github.com/CeruleanBlueSoftware/ceruleans-sound-searcher/releases/download/model-1/"
MODEL_FILES = {
	"MODEL_LICENSE.txt": (11021, "7ee88dc27794f91208166316743a2da21a0d678ee930c0b4cd686184c10ccb3b"),
	"tokenizer.json": (3558802, "4fd1d86b4f5b53f40a609fcd11c1f34024b735f870a07439d70202b98493661a"),
	"clap_mel_filters.npy": (262784, "864000805284942af508f457ee6f5dd900862823797e5852e6ba376550ff766f"),
	"clap_audio.onnx": (279980898, "ae645f77cd869a9e186ce0d9b1a3ed11df72751c571fef37d8b329a2fed8701e"),
	"clap_text.onnx": (501433308, "d2ce275adc7f14c19df06c11433009c9d630bbed2b65aefe6742bc176d1e1a43"),
}
TOTAL_BYTES = sum(size for size, digest in MODEL_FILES.values())
USER_AGENT = "CeruleansSoundSearcher"


class DownloadStopped(Exception):
	"""The user stopped the download."""


def download_size_text():
	"""The download's size in words, such as "about 785 MB"."""
	return f"about {round(TOTAL_BYTES / 1e6)} MB"


def missing_files(folder):
	"""The model files not yet in the folder at their full size."""
	folder = Path(folder)
	return [name for name, (size, digest) in MODEL_FILES.items() if not (folder / name).exists() or (folder / name).stat().st_size != size]


def file_digest(path):
	"""The SHA-256 of a file."""
	digest = hashlib.sha256()
	with open(path, "rb") as handle:
		while chunk := handle.read(1 << 20):
			digest.update(chunk)
	return digest.hexdigest()


def download_model(folder, report=print, should_stop=lambda: False):
	"""Download any missing model files into folder, calling report(text) with progress; raise on failure.

	A download that's stopped or cut off carries on from where it got to next time. The address can be changed with the SOUND_SEARCHER_MODEL_URL environment variable, for testing."""
	folder = Path(folder)
	folder.mkdir(parents=True, exist_ok=True)
	base = os.environ.get("SOUND_SEARCHER_MODEL_URL") or RELEASE_URL
	to_get = missing_files(folder)
	done = sum(MODEL_FILES[name][0] for name in MODEL_FILES if name not in to_get)
	last_percent = -1
	for name in to_get:
		size, expected = MODEL_FILES[name]
		partial = folder / (name + ".part")
		already = partial.stat().st_size if partial.exists() else 0
		if already >= size:
			partial.unlink()
			already = 0
		headers = {"User-Agent": USER_AGENT}
		if already:
			headers["Range"] = f"bytes={already}-"
		request = urllib.request.Request(base + name, headers=headers)
		with urllib.request.urlopen(request, timeout=60) as response:
			if already and response.status != 206:
				already = 0
			done += already
			with open(partial, "ab" if already else "wb") as handle:
				while chunk := response.read(1 << 20):
					if should_stop():
						raise DownloadStopped()
					handle.write(chunk)
					done += len(chunk)
					percent = int(done * 100 / TOTAL_BYTES)
					if percent != last_percent:
						last_percent = percent
						report(f"Downloading the sound model, {percent} percent.")
		if file_digest(partial) != expected:
			partial.unlink(missing_ok=True)
			raise RuntimeError(f"{name} didn't download correctly. Please try again.")
		partial.replace(folder / name)
	report("The sound model is ready.")
