"""The sound index: describes every sound in the library folders with CLAP, keeps it in SQLite, and searches it.

Run this file to build or update the index. Cerulean's Sound Searcher imports it to search.
"""

import re
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

import settings as app_settings

APP_FOLDER = Path(sys.argv[0] if getattr(sys, "frozen", False) else __file__).resolve().parent
LABELS_FILE = APP_FOLDER / "audioset_labels.txt"

MODEL_NAME = "laion/larger_clap_general"
MODEL_RATE = 48000
PIECE_SECONDS = 10
MAX_PIECES = 12
LABELS_PER_SOUND = 3
SILENCE_LEVEL = 0.0005
SOUND_EXTENSIONS = {".wav", ".flac", ".mp3", ".ogg", ".aiff", ".aif"}
KEY_FORMAT = "library name/path"

SCHEMA = """
create table if not exists sounds (
	path text primary key,
	folder text,
	name text,
	size integer,
	modified real,
	duration real,
	channels integer,
	sample_rate integer,
	tags text,
	labels text,
	source text,
	licence text,
	cleared text,
	credit text,
	embedding blob,
	error text,
	indexed text
);
create table if not exists pieces (
	path text,
	start real,
	embedding blob
);
create index if not exists pieces_path on pieces(path);
create table if not exists notes (
	path text primary key,
	notes text,
	updated text
);
create table if not exists meta (
	key text primary key,
	value text
);
"""


def database_file(settings):
	"""Where the index lives, from the settings."""
	return Path(settings["index_file"])


def report_file(settings):
	"""Where the index report is written: beside the index."""
	return database_file(settings).parent / "index report.txt"


def open_database(settings):
	"""Open the index database, creating its folder and tables the first time."""
	path = database_file(settings)
	path.parent.mkdir(parents=True, exist_ok=True)
	connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
	connection.executescript(SCHEMA)
	if connection.execute("select value from meta where key = 'key format'").fetchone() is None:
		if connection.execute("select count(*) from sounds").fetchone()[0]:
			connection.close()
			raise RuntimeError(f"The index {path} was made by an older version of Cerulean's Sound Searcher. Index again into a new file.")
		connection.execute("insert into meta values ('key format', ?)", (KEY_FORMAT,))
		connection.commit()
	return connection


def rename_library(settings, old_name, new_name):
	"""Rename a library's sounds and notes in the index, so nothing needs indexing again."""
	connection = open_database(settings)
	for table in ("sounds", "pieces", "notes"):
		connection.execute(
			f"update {table} set path = ? || substr(path, ?) where path like ? escape '\\'",
			(new_name + "/", len(old_name) + 2, like_prefix(old_name + "/")),
		)
	connection.execute(
		"update sounds set folder = ? || substr(folder, ?) where folder = ? or folder like ? escape '\\'",
		(new_name, len(old_name) + 1, old_name, like_prefix(old_name + "/")),
	)
	connection.commit()
	connection.close()


def like_prefix(text):
	"""A LIKE pattern matching anything that starts with text."""
	return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class ClapModel:
	"""The CLAP model, which turns sounds and text into comparable embeddings."""

	def __init__(self):
		"""Load the model and its processor (downloaded once, then read from the cache)."""
		import torch
		from transformers import ClapModel as HuggingFaceClap, ClapProcessor
		self.torch = torch
		self.model = HuggingFaceClap.from_pretrained(MODEL_NAME).eval()
		self.processor = ClapProcessor.from_pretrained(MODEL_NAME)

	def as_array(self, features):
		"""Turn the model's output into unit-length numpy rows."""
		if not self.torch.is_tensor(features):
			features = features.pooler_output
		features = self.torch.nn.functional.normalize(features, dim=-1)
		return features.numpy().astype(np.float32)

	def embed_audio(self, pieces):
		"""Embed a list of mono 48 kHz pieces, each up to 10 seconds."""
		with self.torch.inference_mode():
			inputs = self.processor(audio=pieces, sampling_rate=MODEL_RATE, return_tensors="pt")
			return self.as_array(self.model.get_audio_features(**inputs))

	def embed_text(self, texts):
		"""Embed a list of descriptions."""
		with self.torch.inference_mode():
			inputs = self.processor(text=texts, return_tensors="pt", padding=True)
			return self.as_array(self.model.get_text_features(**inputs))


def read_audio(path):
	"""Read a sound file as mono 48 kHz samples; return (samples, info) where info has length and format."""
	import soundfile
	import soxr
	with soundfile.SoundFile(str(path)) as sound:
		info = {"duration": sound.frames / sound.samplerate, "channels": sound.channels, "sample_rate": sound.samplerate}
		try:
			metadata = sound.copy_metadata()
		except Exception:
			metadata = {}
		samples = sound.read(dtype="float32", always_2d=True)
	info["tags"] = "; ".join(f"{key}: {value}" for key, value in metadata.items() if value and str(value).strip())
	mono = samples.mean(axis=1)
	if info["sample_rate"] != MODEL_RATE and len(mono):
		mono = soxr.resample(mono, info["sample_rate"], MODEL_RATE)
	return mono.astype(np.float32), info


def cut_pieces(samples):
	"""Cut samples into 10-second pieces, skipping silent ones and keeping at most MAX_PIECES spread evenly."""
	size = PIECE_SECONDS * MODEL_RATE
	starts = list(range(0, max(1, len(samples) - size // 4), size)) or [0]
	pieces = [(start / MODEL_RATE, samples[start:start + size]) for start in starts]
	loud = [piece for piece in pieces if len(piece[1]) and np.sqrt(np.mean(piece[1] ** 2)) > SILENCE_LEVEL]
	pieces = loud or pieces[:1]
	if len(pieces) > MAX_PIECES:
		chosen = np.linspace(0, len(pieces) - 1, MAX_PIECES).round().astype(int)
		pieces = [pieces[i] for i in chosen]
	if not len(pieces[0][1]):
		pieces = [(0.0, np.zeros(MODEL_RATE // 10, dtype=np.float32))]
	return pieces


class LicenceFinder:
	"""Works out where a sound came from and its licence: its own credit file, a Freesound pack readme, or a folder rule."""

	def __init__(self, settings):
		"""Keep the settings and start with an empty cache of pack readmes."""
		self.settings = settings
		self.readmes = {}

	def find(self, path, key):
		"""Return (source, licence, cleared, credit) for a sound file."""
		found = self.from_credit_file(path) or self.from_freesound_pack(path)
		if found:
			return found
		rule = app_settings.rule_for(self.settings, key)
		if rule:
			return rule["source"], rule["licence"], rule["cleared"], rule.get("credit", "")
		return "Unknown", "Unknown", "unknown", ""

	def from_credit_file(self, path):
		"""Read a credit file beside the sound (as Cerulean's Sound Searcher writes for downloads), or None if there isn't one."""
		credit_file = path.with_suffix(".txt")
		if not credit_file.exists():
			return None
		text = credit_file.read_text(encoding="utf-8", errors="replace")
		match = re.search(r"^License: ([^,\n]+)", text, re.MULTILINE)
		if not match:
			return None
		licence = match.group(1).strip()
		source = "Freesound" if "freesound.org" in text else "Credit file"
		return source, licence, licence_cleared(licence), text.splitlines()[0]

	def from_freesound_pack(self, path):
		"""Find a Freesound pack's _readme_and_license.txt above the sound and read its licence, or None."""
		_, library_folder = app_settings.library_for(self.settings, path)
		folder = path.parent
		while library_folder and (folder == library_folder or library_folder in folder.parents):
			readme = folder / "_readme_and_license.txt"
			if readme.exists():
				pack = self.read_readme(readme)
				sound_id = re.match(r"(\d+)__", path.name)
				entry = pack["sounds"].get(sound_id.group(1)) if sound_id else None
				if entry:
					licence, url = entry["licence"], entry["url"]
				elif len(pack["licences"]) == 1:
					licence, url = pack["licences"][0], pack["url"]
				else:
					licence, url = "Unknown (not listed in the pack's readme)", pack["url"]
				creator, title = name_parts(path.stem, pack["creator"])
				credit = f'"{title}" by {creator} on Freesound ({url}), licensed under {licence}.'
				return "Freesound", licence, licence_cleared(licence), credit
			folder = folder.parent
		return None

	def read_readme(self, readme):
		"""Read a Freesound pack readme into its creator, link, licences and per-sound licences by sound ID (cached)."""
		if readme in self.readmes:
			return self.readmes[readme]
		text = readme.read_text(encoding="utf-8", errors="replace")
		creator = re.search(r"^ - (.+?) \(", text, re.MULTILINE)
		url = re.search(r"pack online at: (\S+)", text)
		licence_section = re.search(r"License details\s*-+\s*(.*?)\n\s*\n\s*\n", text, re.DOTALL)
		licences = re.findall(r"^(.+?): http", licence_section.group(1), re.MULTILINE) if licence_section else []
		sounds = {}
		for name, sound_url, sound_licence in re.findall(r"\* (.+?)\n\s+\* url: (\S+)\n\s+\* license: (.+)", text):
			sound_id = re.match(r"(\d+)__", name.strip())
			if sound_id:
				sounds[sound_id.group(1)] = {"url": sound_url, "licence": sound_licence.strip()}
		pack = {
			"creator": creator.group(1) if creator else "unknown",
			"url": url.group(1) if url else "https://freesound.org",
			"licences": [licence.strip() for licence in licences],
			"sounds": sounds,
		}
		self.readmes[readme] = pack
		return pack


def name_parts(stem, pack_creator):
	"""Freesound pack files are named id__creator__title; return (creator, title), falling back to the pack's creator."""
	match = re.match(r"\d+__(.+?)__(.+)", stem)
	if match:
		return match.group(1), match.group(2)
	return pack_creator, stem


def licence_cleared(licence):
	"""yes for licences that allow commercial use, no for those that don't, unknown otherwise."""
	lowered = licence.lower()
	if "noncommercial" in lowered or "non-commercial" in lowered or "sampling" in lowered or "by-nc" in lowered:
		return "no"
	if "creative commons 0" in lowered or "cc0" in lowered or "attribution" in lowered or lowered.startswith("cc by"):
		return "yes"
	return "unknown"


def notes_from_credit_file(path):
	"""Return the Notes: line from a credit file beside a sound, or an empty string."""
	credit_file = path.with_suffix(".txt")
	if not credit_file.exists():
		return ""
	match = re.search(r"^Notes: (.+)$", credit_file.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
	return match.group(1).strip() if match else ""


def find_sound_files(settings, only=None):
	"""Every sound file in the library folders, or in one folder or file of them, as {key: path}, skipping Mac leftovers."""
	files = {}
	if only and Path(only).is_file():
		key = app_settings.key_for(settings, only)
		return {key: Path(only)} if key and is_sound_file(Path(only)) else {}
	folders = [Path(only)] if only else [Path(library["folder"]) for library in settings["libraries"]]
	for folder in folders:
		if not folder.is_dir():
			continue
		for path in folder.rglob("*"):
			if is_sound_file(path) and path.is_file():
				key = app_settings.key_for(settings, path)
				if key:
					files[key] = path
	return dict(sorted(files.items()))


def is_sound_file(path):
	"""True for a file with a sound extension that isn't a Mac leftover."""
	return path.suffix.lower() in SOUND_EXTENSIONS and not path.name.startswith("._")


def pending_changes(settings):
	"""Count the library's sounds that are new or changed since indexing, and those that are gone, as (changed, gone)."""
	if not Path(settings["index_file"]).exists():
		return len(find_sound_files(settings)), 0
	connection = open_database(settings)
	known = {row[0]: (row[1], row[2]) for row in connection.execute("select path, size, modified from sounds")}
	connection.close()
	current = find_sound_files(settings)
	changed = 0
	for key, path in current.items():
		stat = path.stat()
		if known.get(key) != (stat.st_size, stat.st_mtime):
			changed += 1
	return changed, len([key for key in known if key not in current])


def backup_index(settings, destination):
	"""Copy the index safely, even while it's in use, to destination."""
	source = open_database(settings)
	target = sqlite3.connect(destination)
	source.backup(target)
	target.close()
	source.close()


def check_index_file(path):
	"""Raise RuntimeError unless path is an index this version can use (or an empty database)."""
	connection = sqlite3.connect(path)
	try:
		tables = {row[0] for row in connection.execute("select name from sqlite_master where type = 'table'")}
		if tables and "sounds" not in tables:
			raise RuntimeError(f"{Path(path).name} isn't a sound index.")
		if "sounds" in tables:
			has_key_format = "meta" in tables and connection.execute("select value from meta where key = 'key format'").fetchone()
			if not has_key_format and connection.execute("select count(*) from sounds").fetchone()[0]:
				raise RuntimeError(f"{Path(path).name} was made by an older version and can't be used.")
	except sqlite3.DatabaseError as error:
		raise RuntimeError(f"{Path(path).name} isn't a sound index ({error}).")
	finally:
		connection.close()


def load_labels(model):
	"""Embed the AudioSet category names once, for labelling sounds."""
	names = [line.strip() for line in LABELS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
	embeddings = np.concatenate([model.embed_text([f"the sound of {name.lower()}" for name in names[i:i + 64]]) for i in range(0, len(names), 64)])
	return names, embeddings


def describe_duration(seconds):
	"""Say a length the way a person would, for the progress report."""
	seconds = int(seconds)
	hours, rest = divmod(seconds, 3600)
	minutes = rest // 60
	return f"{hours} hours {minutes} minutes" if hours else f"{minutes} minutes"


def build_index(settings=None, only=None, report=print, should_stop=lambda: False, model=None):
	"""Index new and changed sounds in the library folders, drop ones that are gone, and write a report.

	only limits the run to one folder or file; report(text) receives progress messages; should_stop() is
	checked between sounds; model is an already loaded ClapModel to reuse. Returns (indexed, errors, stopped)."""
	settings = settings or app_settings.load()
	started = time.time()
	connection = open_database(settings)
	current = find_sound_files(settings, only)
	known = {row[0]: (row[1], row[2]) for row in connection.execute("select path, size, modified from sounds")}
	if only and Path(only).is_file():
		gone = []
	else:
		prefix = app_settings.key_for(settings, only) + "/" if only else ""
		gone = [key for key in known if key.startswith(prefix) and key not in current]
	for key in gone:
		connection.execute("delete from sounds where path = ?", (key,))
		connection.execute("delete from pieces where path = ?", (key,))
	to_do = []
	for key, path in current.items():
		stat = path.stat()
		if known.get(key) != (stat.st_size, stat.st_mtime):
			to_do.append((key, path, stat))
	licences = LicenceFinder(settings)
	waiting = {key for key, path, stat in to_do}
	for key, path in current.items():
		if key in known and key not in waiting:
			connection.execute(
				"update sounds set source = ?, licence = ?, cleared = ?, credit = ? where path = ?",
				(*licences.find(path, key), key),
			)
			import_notes(connection, key, path)
	connection.commit()
	report(f"{len(current)} sounds in the library. {len(to_do)} to index, {len(gone)} removed.")
	errors = []
	indexed = 0
	stopped = False
	if to_do:
		if model is None:
			report("Loading the sound model...")
			model = ClapModel()
		label_names, label_embeddings = load_labels(model)
		for number, (key, path, stat) in enumerate(to_do, 1):
			if should_stop():
				stopped = True
				break
			error = index_one(connection, model, label_names, label_embeddings, licences, key, path, stat)
			indexed = number
			if error:
				errors.append(f"{key}: {error}")
			if number % 25 == 0 or number == len(to_do):
				connection.commit()
				elapsed = time.time() - started
				left = elapsed / number * (len(to_do) - number)
				report(f"{number} of {len(to_do)} done, about {describe_duration(left)} left.")
	connection.commit()
	if not only:
		write_report(settings, connection, indexed, errors, started, stopped)
	connection.close()
	if stopped:
		report(f"Stopped after {indexed} of {len(to_do)}. Update the index again to carry on.")
	elif not only:
		report(f"Done. Report written to {report_file(settings)}.")
	return indexed, errors, stopped


def index_one(connection, model, label_names, label_embeddings, licences, key, path, stat):
	"""Describe one sound and store it; return an error message if it couldn't be read."""
	source, licence, cleared, credit = licences.find(path, key)
	row = {
		"path": key, "folder": key.rpartition("/")[0], "name": path.stem,
		"size": stat.st_size, "modified": stat.st_mtime, "source": source, "licence": licence,
		"cleared": cleared, "credit": credit, "indexed": time.strftime("%Y-%m-%d %H:%M"),
		"duration": None, "channels": None, "sample_rate": None, "tags": "", "labels": "",
		"embedding": None, "error": None,
	}
	pieces_rows = []
	try:
		samples, info = read_audio(path)
		row.update(info)
		pieces = cut_pieces(samples)
		embeddings = model.embed_audio([piece for start, piece in pieces])
		overall = embeddings.mean(axis=0)
		overall /= np.linalg.norm(overall) or 1
		row["embedding"] = overall.astype(np.float32).tobytes()
		scores = label_embeddings @ overall
		best = np.argsort(-scores)[:LABELS_PER_SOUND]
		row["labels"] = ", ".join(label_names[i] for i in best)
		pieces_rows = [(key, start, embedding.tobytes()) for (start, _), embedding in zip(pieces, embeddings)]
	except Exception as error:
		row["error"] = str(error) or error.__class__.__name__
	connection.execute("delete from pieces where path = ?", (key,))
	connection.executemany("insert into pieces values (?, ?, ?)", pieces_rows)
	connection.execute(
		"insert or replace into sounds values (:path, :folder, :name, :size, :modified, :duration, :channels, "
		":sample_rate, :tags, :labels, :source, :licence, :cleared, :credit, :embedding, :error, :indexed)",
		row,
	)
	import_notes(connection, key, path)
	return row["error"]


def import_notes(connection, key, path):
	"""Copy a Notes: line from a sound's credit file into the index, unless the sound already has notes."""
	notes = notes_from_credit_file(path)
	if notes:
		connection.execute("insert or ignore into notes values (?, ?, ?)", (key, notes, time.strftime("%Y-%m-%d %H:%M")))


def write_report(settings, connection, indexed, errors, started, stopped=False):
	"""Write the index report: what was indexed, licence counts and any files that couldn't be read."""
	total = connection.execute("select count(*) from sounds").fetchone()[0]
	cleared = dict(connection.execute("select cleared, count(*) from sounds group by cleared").fetchall())
	unknown_folders = connection.execute(
		"select folder, count(*) from sounds where cleared = 'unknown' group by folder order by folder"
	).fetchall()
	lines = [
		f"Sound index report, {time.strftime('%d %B %Y, %H:%M')}",
		"",
		f"Sounds in the index: {total}. Indexed this run: {indexed}, in {describe_duration(time.time() - started)}."
		+ (" The run was stopped before the end." if stopped else ""),
		f"Licence cleared for commercial use: {cleared.get('yes', 0)}. Not allowed or source not recorded: {cleared.get('no', 0)}. Licence unknown: {cleared.get('unknown', 0)}.",
		"",
	]
	if unknown_folders:
		lines.append("Folders with no licence information (add a licence rule for them in Settings):")
		lines += [f"\t{folder} ({count} sounds)" for folder, count in unknown_folders]
		lines.append("")
	lines.append(f"Files that couldn't be read: {len(errors)}.")
	lines += [f"\t{error}" for error in errors]
	report_file(settings).write_text("\n".join(lines) + "\n", encoding="utf-8")


class SoundIndex:
	"""Searches the index: by description, by your notes, and for sounds like a given one."""

	def __init__(self, settings, model=None):
		"""Read every embedding into memory; the CLAP model is loaded later, when first needed."""
		self.settings = settings
		self.model = model
		self.note_cache = {}
		self.connection = open_database(settings)
		self.loaded_signature = self.signature()
		rows = self.connection.execute("select path, embedding from sounds where embedding is not null").fetchall()
		self.paths = [row[0] for row in rows]
		self.position = {path: i for i, path in enumerate(self.paths)}
		self.embeddings = np.frombuffer(b"".join(row[1] for row in rows), dtype=np.float32).reshape(len(rows), -1) if rows else np.zeros((0, 512), np.float32)
		piece_rows = self.connection.execute("select path, embedding from pieces").fetchall()
		self.piece_owner = np.array([self.position.get(row[0], -1) for row in piece_rows], dtype=np.int64)
		self.piece_embeddings = np.frombuffer(b"".join(row[1] for row in piece_rows), dtype=np.float32).reshape(len(piece_rows), -1) if piece_rows else np.zeros((0, 512), np.float32)

	def signature(self):
		"""A quick fingerprint of the sounds table, which changes whenever the indexer adds or updates sounds."""
		return self.connection.execute("select count(*), max(indexed) from sounds").fetchone()

	def is_stale(self):
		"""True when the indexer has changed the database since these embeddings were read."""
		return self.signature() != self.loaded_signature

	def ensure_model(self):
		"""Load the CLAP model the first time a description search needs it."""
		if self.model is None:
			self.model = ClapModel()
		return self.model

	def best_scores(self, query_embedding):
		"""Score every sound by its best-matching piece."""
		scores = np.full(len(self.paths), -1.0, dtype=np.float32)
		if len(self.piece_embeddings):
			piece_scores = self.piece_embeddings @ query_embedding
			valid = self.piece_owner >= 0
			np.maximum.at(scores, self.piece_owner[valid], piece_scores[valid])
		return scores

	def note_embeddings(self, texts):
		"""Embed notes, remembering each one so it's only worked out once."""
		missing = [text for text in texts if text not in self.note_cache]
		if missing:
			for text, embedding in zip(missing, self.model.embed_text(missing)):
				self.note_cache[text] = embedding
		return np.array([self.note_cache[text] for text in texts])

	def search(self, text, limit=50, cleared_only=False, max_duration=None, library=None):
		"""Find sounds matching a description: notes that share its words first, then by meaning."""
		query = self.ensure_model().embed_text([text])[0]
		scores = self.best_scores(query)
		notes = self.all_notes()
		note_paths = [path for path in notes if path in self.position]
		if note_paths:
			note_scores = self.note_embeddings([notes[path] for path in note_paths]) @ query
			words = set(re.findall(r"\w+", text.lower()))
			for path, score in zip(note_paths, note_scores):
				note_words = set(re.findall(r"\w+", notes[path].lower()))
				bonus = 0.25 * len(words & note_words) / max(1, len(words))
				index = self.position[path]
				scores[index] = max(scores[index], float(score)) + bonus
		return self.ranked(scores, limit, cleared_only, max_duration, library)

	def similar(self, path, limit=50, cleared_only=False, max_duration=None, library=None):
		"""Find the sounds closest to a given one."""
		if path not in self.position:
			return []
		scores = self.embeddings @ self.embeddings[self.position[path]]
		scores[self.position[path]] = -2
		return self.ranked(scores, limit, cleared_only, max_duration, library)

	def ranked(self, scores, limit, cleared_only, max_duration, library=None):
		"""Turn scores into a list of (score, details) for the best sounds, within the licence, length and library limits."""
		results = []
		for index in np.argsort(-scores):
			if library and not self.paths[index].startswith(library + "/"):
				continue
			details = self.details(self.paths[index])
			if details.get("full_path") is None:
				continue
			if cleared_only and details.get("cleared") != "yes":
				continue
			if max_duration and (details.get("duration") or 0) > max_duration:
				continue
			results.append((float(scores[index]), details))
			if len(results) >= limit:
				break
		return results

	def details(self, path):
		"""Everything stored about one sound, with its notes, as a dictionary."""
		cursor = self.connection.execute(
			"select path, folder, name, duration, channels, sample_rate, tags, labels, source, licence, cleared, credit from sounds where path = ?",
			(path,),
		)
		names = [column[0] for column in cursor.description]
		row = cursor.fetchone()
		details = dict(zip(names, row)) if row else {"path": path}
		if details.get("name"):
			details["name"] = name_parts(details["name"], "")[1]
		details["notes"] = self.notes(path)
		details["full_path"] = app_settings.path_for(self.settings, path)
		return details

	def all_notes(self):
		"""Every sound's notes, keyed by path."""
		return {path: text for path, text in self.connection.execute("select path, notes from notes where notes != ''")}

	def notes(self, path):
		"""One sound's notes, or an empty string."""
		row = self.connection.execute("select notes from notes where path = ?", (path,)).fetchone()
		return row[0] if row else ""

	def set_notes(self, path, text):
		"""Save (or clear) one sound's notes."""
		self.connection.execute("insert or replace into notes values (?, ?, ?)", (path, text.strip(), time.strftime("%Y-%m-%d %H:%M")))
		self.connection.commit()

	def downloaded_freesound_ids(self):
		"""Freesound sound IDs among the library's file names, such as '813651 old door.wav'."""
		ids = set()
		for (name,) in self.connection.execute("select name from sounds"):
			match = re.match(r"(\d+) ", name or "")
			if match:
				ids.add(int(match.group(1)))
		return ids


if __name__ == "__main__":
	build_index(report=lambda text: print(text, flush=True))
