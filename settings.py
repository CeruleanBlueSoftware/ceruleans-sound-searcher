"""Settings for Cerulean's Sound Searcher: credentials, folders, Freesound filters and licence rules, kept in the user's own app data folder."""

import json
import os
from pathlib import Path

SETTINGS_FOLDER = Path(os.environ.get("APPDATA") or Path.home() / ".config") / "Cerulean Blue Software" / "Cerulean's Sound Searcher"
SETTINGS_FILE = SETTINGS_FOLDER / "settings.json"
TOKEN_FILE = SETTINGS_FOLDER / "oauth_token.json"
CREDENTIALS_PAGE = "https://freesound.org/apiv2/apply"

LICENCE_CHOICES = [
	("cc0", "Creative Commons 0 (no credit needed)", 'license:"Creative Commons 0"'),
	("by", "Attribution (credit the creator)", 'license:"Attribution"'),
	("by-nc", "Attribution Noncommercial (not for games you sell)", 'license:"Attribution Noncommercial"'),
]
CHANNEL_CHOICES = [("any", "Any"), ("mono", "Mono only"), ("stereo", "Stereo only")]
SAMPLE_RATE_CHOICES = [("any", "Any"), ("44100", "44,100 Hz only"), ("48000", "48,000 Hz only")]
CLEARED_CHOICES = [("yes", "Yes, allowed in games I sell"), ("no", "No, not allowed"), ("unknown", "Unknown")]


def default_settings():
	"""The settings a new user starts with."""
	return {
		"freesound_client_id": "",
		"freesound_client_secret": "",
		"freesound_licences": ["cc0", "by"],
		"freesound_channels": "any",
		"freesound_sample_rate": "any",
		"downloads_folder": str(Path.home() / "Documents" / "Cerulean's Sound Searcher downloads"),
		"libraries": [],
		"licence_rules": [],
		"index_file": str(SETTINGS_FOLDER / "sound_index.db"),
	}


def load():
	"""Read the settings, filling in anything missing with the defaults."""
	settings = default_settings()
	if SETTINGS_FILE.exists():
		try:
			settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
		except (OSError, json.JSONDecodeError):
			pass
	return settings


def save(settings):
	"""Write the settings file, creating the app data folder if needed."""
	SETTINGS_FOLDER.mkdir(parents=True, exist_ok=True)
	SETTINGS_FILE.write_text(json.dumps(settings, indent="\t"), encoding="utf-8")


def has_freesound_credentials(settings):
	"""True when both parts of the Freesound credentials are filled in."""
	return bool(settings["freesound_client_id"].strip() and settings["freesound_client_secret"].strip())


def freesound_filter(settings):
	"""Build the Freesound search filter from the chosen licences, channels and sample rate."""
	licences = [query for key, label, query in LICENCE_CHOICES if key in settings["freesound_licences"]]
	parts = ["(" + " OR ".join(licences) + ")"] if licences else []
	if settings["freesound_channels"] == "mono":
		parts.append("channels:1")
	elif settings["freesound_channels"] == "stereo":
		parts.append("channels:2")
	if settings["freesound_sample_rate"] != "any":
		parts.append("samplerate:" + settings["freesound_sample_rate"])
	return " ".join(parts)


def library_for(settings, path):
	"""Return the library a file is in, as (name, folder), or (None, None)."""
	path = Path(path).resolve()
	for library in settings["libraries"]:
		folder = Path(library["folder"]).resolve()
		if path == folder or folder in path.parents:
			return library["name"], folder
	return None, None


def key_for(settings, path):
	"""A sound's key in the index: its library's name, then its path inside that library."""
	name, folder = library_for(settings, path)
	if name is None:
		return None
	return name + "/" + Path(path).resolve().relative_to(folder).as_posix()


def path_for(settings, key):
	"""The file a sound's key points to, or None if its library is gone."""
	name, _, rest = key.partition("/")
	for library in settings["libraries"]:
		if library["name"] == name:
			return Path(library["folder"]) / rest
	return None


def rule_for(settings, key):
	"""The licence rule for the most specific folder holding a sound, or None."""
	best = None
	for rule in settings["licence_rules"]:
		folder = rule["folder"].rstrip("/") + "/"
		if key.startswith(folder) and (best is None or len(folder) > len(best["folder"].rstrip("/") + "/")):
			best = rule
	return best

