"""Cerulean's Sound Searcher: find, preview and download Freesound sounds, and search your own sound library, with credits."""

import base64
import hashlib
import html
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import wx

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

import settings as app_settings

APP_NAME = "Cerulean's Sound Searcher"
APP_VERSION = "0.3.0"
USER_AGENT = "CeruleansSoundSearcher/" + APP_VERSION

LIBRARY_PAGE_SIZE = 50
SEARCH_PLACES = ["Freesound", "My library"]
ALL_LIBRARIES = "All libraries"
CREDITS_LIST_FILE = app_settings.SETTINGS_FOLDER / "credits list.json"

API_ROOT = "https://freesound.org/apiv2/"
SEARCH_URL = API_ROOT + "search/"
SOUND_URL = API_ROOT + "sounds/{sound_id}/"
DOWNLOAD_URL = API_ROOT + "sounds/{sound_id}/download/"
LOOKUP_GAP_SECONDS = 1.1
FREESOUND_NUMBER = re.compile(r"^(\d+)(?:__| )")
AUTHORIZE_URL = API_ROOT + "oauth2/authorize/"
ACCESS_TOKEN_URL = API_ROOT + "oauth2/access_token/"
SOUND_PAGE_URL = "https://freesound.org/s/{sound_id}/"

CALLBACK_PORT = 8765
CALLBACK_ADDRESS = f"http://localhost:{CALLBACK_PORT}/callback"

PAGE_SIZE = 20
FIELDS = ",".join([
	"id", "name", "username", "license", "description", "duration",
	"samplerate", "channels", "type", "filesize", "num_downloads",
	"avg_rating", "tags", "url", "previews"
])

SORT_CHOICES = [
	("Best match", "score"),
	("Most downloaded", "downloads_desc"),
	("Top rated", "rating_desc"),
	("Shortest first", "duration_asc"),
	("Newest first", "created_desc"),
]

DURATION_CHOICES = [
	("Any length", None),
	("Up to 2 seconds", 2),
	("Up to 5 seconds", 5),
	("Up to 10 seconds", 10),
	("Up to 30 seconds", 30),
	("Up to 1 minute", 60),
	("Up to 5 minutes", 300),
]

KEYS_HELP = (
	"Keys\n\n"
	"F2: settings: your Freesound credentials, folders and licence rules.\n"
	"Ctrl+1: search Freesound.\n"
	"Ctrl+2: search my library, the sounds in your library folders.\n"
	"Enter in the search box: search.\n"
	"Space in the results list: play or stop the sound.\n"
	"Escape: stop the sound.\n"
	"Ctrl+D: download the selected Freesound sound with its credit, and notes if you add them.\n"
	"Ctrl+M: find library sounds like the selected one.\n"
	"Ctrl+U: update the library index, or stop an update.\n"
	"Ctrl+K: add the selected sound's credit to the credits list; the File menu shows, saves or clears it.\n"
	"Ctrl+Shift+A: move the selected download, with its credit file, into a library folder.\n"
	"Ctrl+E: edit the selected library sound's notes.\n"
	"Ctrl+N: load more results.\n"
	"Ctrl+O: open the sound's Freesound page, or for a library sound, its folder.\n"
	"Ctrl+Shift+C: copy the credit line.\n"
	"Ctrl+L: open the downloads folder.\n"
	"Ctrl+F: back to the search box.\n"
	"Tab: move between the search box, filters, results and details."
)


_screen_reader = None


def screen_reader():
	"""The user's screen reader through accessible_output2, found once; None if there isn't one."""
	global _screen_reader
	if _screen_reader is None:
		try:
			import accessible_output2.outputs.auto
			_screen_reader = accessible_output2.outputs.auto.Auto()
		except Exception:
			_screen_reader = False
	return _screen_reader or None


def speak(text, interrupt=True):
	"""Say text through the screen reader, and show it in braille when there's a display."""
	output = screen_reader()
	if output is None:
		return
	try:
		output.output(text, interrupt=interrupt)
	except Exception:
		pass


class FreesoundError(Exception):
	"""A problem talking to Freesound, worded for the user."""


def describe_http_error(error):
	"""Turn an HTTP error from Freesound into a short sentence."""
	try:
		reply = json.loads(error.read().decode("utf-8", errors="replace"))
		detail = reply.get("detail") or reply.get("error_description") or reply.get("error") or ""
	except Exception:
		detail = ""
	message = f"Freesound returned error {error.code}."
	if detail:
		message += " " + str(detail)
	return message


def open_url(request, timeout=30):
	"""Open a request, converting network failures into FreesoundError."""
	try:
		return urllib.request.urlopen(request, timeout=timeout)
	except urllib.error.HTTPError:
		raise
	except urllib.error.URLError as error:
		raise FreesoundError(f"Network error: {error.reason}")
	except TimeoutError:
		raise FreesoundError("Freesound took too long to answer.")


def fetch_json(request, timeout=30):
	"""Open a request and decode its JSON reply."""
	try:
		with open_url(request, timeout) as response:
			return json.loads(response.read().decode("utf-8"))
	except urllib.error.HTTPError as error:
		raise FreesoundError(describe_http_error(error))
	except json.JSONDecodeError:
		raise FreesoundError("Freesound sent back something that wasn't valid data.")


def license_short_name(license_url):
	"""Give a readable name, such as CC BY 4.0, for a Creative Commons license address."""
	license_url = license_url or ""
	match = re.search(r"/licenses/by/(\d+\.\d+)", license_url)
	if match:
		return "CC BY " + match.group(1)
	match = re.search(r"/publicdomain/zero/(\d+\.\d+)", license_url)
	if match:
		return "CC0 " + match.group(1)
	match = re.search(r"/licenses/by-nc/(\d+\.\d+)", license_url)
	if match:
		return "CC BY-NC " + match.group(1) + " (not for commercial use)"
	if "sampling+" in license_url.lower():
		return "Sampling+ (not for commercial use)"
	return "see license link"


def sound_page(sound):
	"""Return the Freesound web page address for a sound."""
	return sound.get("url") or SOUND_PAGE_URL.format(sound_id=sound.get("id", ""))


def format_duration(seconds):
	"""Format a length in seconds as minutes:seconds, or tenths for very short sounds."""
	seconds = float(seconds or 0)
	if seconds < 10:
		return f"{seconds:.1f} seconds"
	whole = int(round(seconds))
	return f"{whole // 60}:{whole % 60:02d}"


def format_size(size):
	"""Format a file size in bytes as KB or MB."""
	size = int(size or 0)
	if size >= 1024 * 1024:
		return f"{size / (1024 * 1024):.1f} MB"
	if size >= 1024:
		return f"{size / 1024:.0f} KB"
	return f"{size} bytes"


def clean_text(text):
	"""Strip HTML tags and entities from a Freesound description."""
	text = re.sub(r"<br\s*/?>", "\n", text or "", flags=re.IGNORECASE)
	text = re.sub(r"<[^>]+>", "", text)
	return html.unescape(text).strip()


def make_credit_line(sound):
	"""Build the one-line credit for a sound."""
	name = sound.get("name") or "Untitled sound"
	username = sound.get("username") or "unknown uploader"
	license_url = sound.get("license") or ""
	return (
		f'"{name}" by {username} on Freesound ({sound_page(sound)}), '
		f"licensed under {license_short_name(license_url)} ({license_url})."
	)


def strip_extension(name):
	"""Remove a trailing audio file extension, such as .wav, from a sound's name."""
	return re.sub(r"\.(wav|aiff?|flac|ogg|mp3|m4a)$", "", name or "", flags=re.IGNORECASE)


def make_list_line(sound):
	"""Build the short line shown for a sound in the results list."""
	return (
		f"{strip_extension(sound.get('name')) or 'Untitled'}, {format_duration(sound.get('duration'))}, "
		f"{license_short_name(sound.get('license'))}, by {sound.get('username', 'unknown')}"
	)


def make_details(sound):
	"""Build the full details text for a sound."""
	tags = ", ".join(sound.get("tags") or []) or "none"
	rating = sound.get("avg_rating")
	rating_text = f"{rating:.1f} out of 5" if rating else "not rated"
	lines = [
		f"Name: {sound.get('name', 'Untitled')}",
		f"By: {sound.get('username', 'unknown')}",
		f"Length: {format_duration(sound.get('duration'))}",
		f"License: {license_short_name(sound.get('license'))}",
		f"Original file: {str(sound.get('type', '')).upper()}, {format_size(sound.get('filesize'))}, "
		f"{int(sound.get('samplerate') or 0)} Hz, {sound.get('channels', '?')} channels",
		f"Downloads: {sound.get('num_downloads', 0)}. Rating: {rating_text}.",
		f"Tags: {tags}",
		"",
		"Description:",
		clean_text(sound.get("description")) or "No description.",
		"",
		"Credit:",
		make_credit_line(sound),
	]
	return "\n".join(lines)


def licence_status(details):
	"""Say whether a library sound may be used: its licence, or a warning when it isn't cleared."""
	licence = details.get("licence") or "unknown"
	if details.get("cleared") == "yes":
		return licence
	if details.get("cleared") == "no":
		return f"not cleared, {licence}"
	return "licence unknown"


def make_library_line(details):
	"""Build the short results line for a sound in the library."""
	line = (
		f"{details.get('name') or 'Untitled'}, {format_duration(details.get('duration'))}, "
		f"{details.get('labels') or 'no labels'}, {licence_status(details)}"
	)
	if details.get("notes"):
		line += ", has notes"
	return line


def make_library_details(details):
	"""Build the full details text for a sound in the library."""
	lines = [
		f"Name: {details.get('name', 'Untitled')}",
		f"Notes: {details.get('notes') or 'none'}",
		f"Sounds like: {details.get('labels') or 'no labels'}",
		f"Length: {format_duration(details.get('duration'))}",
		f"Format: {int(details.get('sample_rate') or 0)} Hz, {details.get('channels', '?')} channels",
		f"Folder: {details.get('folder', '')}",
		f"Source: {details.get('source') or 'unknown'}",
		f"Licence: {licence_status(details)}",
		f"Tags in the file: {details.get('tags') or 'none'}",
		"",
		"Credit:",
		details.get("credit") or "No credit recorded.",
	]
	return "\n".join(lines)


def load_sound_file(path):
	"""Load a sound file for playing, decoding it with soundfile when pygame can't read it."""
	try:
		return pygame.mixer.Sound(file=str(path))
	except pygame.error:
		import soundfile
		data, rate = soundfile.read(str(path), dtype="int16", always_2d=True)
		buffer = io.BytesIO()
		soundfile.write(buffer, data, rate, format="WAV", subtype="PCM_16")
		buffer.seek(0)
		return pygame.mixer.Sound(file=buffer)


def show_in_folder(path):
	"""Open the file manager at a sound's folder, with the sound selected where the system allows."""
	if sys.platform == "win32":
		subprocess.Popen(["explorer", "/select,", str(path)])
	else:
		wx.LaunchDefaultApplication(str(path.parent))


def safe_file_name(sound):
	"""Make a file name like '813651 old door creak.wav' that's safe on Windows and Linux."""
	file_type = str(sound.get("type") or "wav").lower()
	stem = strip_extension(sound.get("name") or "sound")
	stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", stem)
	stem = " ".join(stem.split()).strip(" .")[:80] or "sound"
	return f"{sound.get('id')} {stem}.{file_type}"


class FreesoundClient:
	"""Talks to the Freesound API: searching, previews, signing in and downloading originals."""

	def __init__(self, settings):
		"""Keep the settings, which hold the credentials, and load any saved sign-in token."""
		self.settings = settings
		self.token = self.load_token()
		self.code_verifier = ""

	@property
	def api_key(self):
		"""The client secret from the user's Freesound API credentials."""
		return self.settings["freesound_client_secret"].strip()

	@property
	def client_id(self):
		"""The client ID from the user's Freesound API credentials."""
		return self.settings["freesound_client_id"].strip()

	def forget_sign_in(self):
		"""Drop the saved sign-in, for when the credentials change."""
		self.token = None
		app_settings.TOKEN_FILE.unlink(missing_ok=True)

	def search(self, query, page, sort, max_duration):
		"""Search Freesound with the licence, channel and sample rate filters from the settings."""
		if not self.api_key:
			raise FreesoundError(
				"To search Freesound, add your own Freesound API credentials in Settings (F2). "
				"Searching your library (Ctrl+2) works without them."
			)
		search_filter = app_settings.freesound_filter(self.settings)
		if max_duration:
			search_filter += f" duration:[0 TO {max_duration}]"
		params = {
			"query": query,
			"filter": search_filter,
			"fields": FIELDS,
			"sort": sort,
			"page_size": PAGE_SIZE,
			"page": page,
		}
		request = urllib.request.Request(
			SEARCH_URL + "?" + urllib.parse.urlencode(params),
			headers={"Authorization": "Token " + self.api_key, "User-Agent": USER_AGENT},
		)
		return fetch_json(request)

	def sound_info(self, sound_id):
		"""Ask Freesound for one sound's name, creator, licence and page; None if it's no longer on Freesound.

		Raises FreesoundError with code "busy" when Freesound asks the app to slow down."""
		request = urllib.request.Request(
			SOUND_URL.format(sound_id=sound_id) + "?" + urllib.parse.urlencode({"fields": "id,name,username,license,url"}),
			headers={"Authorization": "Token " + self.api_key, "User-Agent": USER_AGENT},
		)
		try:
			with open_url(request) as response:
				return json.loads(response.read().decode("utf-8"))
		except urllib.error.HTTPError as error:
			if error.code == 404:
				return None
			if error.code == 429:
				raise FreesoundError("busy")
			raise FreesoundError(describe_http_error(error))

	def fetch_preview(self, sound):
		"""Download a sound's high quality OGG preview into memory and return the bytes."""
		previews = sound.get("previews") or {}
		address = previews.get("preview-hq-ogg") or previews.get("preview-lq-ogg")
		if not address:
			raise FreesoundError("This sound has no preview.")
		request = urllib.request.Request(address, headers={"User-Agent": USER_AGENT})
		try:
			with open_url(request) as response:
				return response.read()
		except urllib.error.HTTPError as error:
			raise FreesoundError(describe_http_error(error))

	def load_token(self):
		"""Read the saved sign-in token, if there is one."""
		try:
			with app_settings.TOKEN_FILE.open("r", encoding="utf-8") as file:
				return json.load(file)
		except (OSError, json.JSONDecodeError):
			return None

	def save_token(self, reply):
		"""Store a token reply from Freesound, noting when it runs out."""
		reply["expires_at"] = time.time() + int(reply.get("expires_in", 86400)) - 60
		self.token = reply
		app_settings.TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
		with app_settings.TOKEN_FILE.open("w", encoding="utf-8") as file:
			json.dump(reply, file, indent="\t")

	def request_token(self, fields):
		"""Ask Freesound for a token with the given form fields and save it."""
		fields = dict(fields, client_id=self.client_id, client_secret=self.api_key)
		request = urllib.request.Request(
			ACCESS_TOKEN_URL,
			data=urllib.parse.urlencode(fields).encode("utf-8"),
			headers={"User-Agent": USER_AGENT},
		)
		self.save_token(fetch_json(request))

	def authorize_address(self):
		"""Return the Freesound page where the user allows the app to download, with a new PKCE challenge."""
		self.code_verifier = secrets.token_urlsafe(64)
		digest = hashlib.sha256(self.code_verifier.encode("ascii")).digest()
		code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
		return AUTHORIZE_URL + "?" + urllib.parse.urlencode({
			"client_id": self.client_id,
			"response_type": "code",
			"state": "cbs",
			"code_challenge": code_challenge,
			"code_challenge_method": "S256",
		})

	def sign_in_with_code(self, code):
		"""Trade the code from the Authorize page, and the matching PKCE verifier, for a sign-in token."""
		self.request_token({
			"grant_type": "authorization_code",
			"code": code,
			"code_verifier": self.code_verifier,
		})

	def has_working_token(self):
		"""Return True if there's a token that hasn't run out, renewing it when possible."""
		if not self.token:
			return False
		if time.time() < self.token.get("expires_at", 0):
			return True
		return self.refresh_token()

	def refresh_token(self):
		"""Renew the sign-in token without the browser; return False if that fails."""
		refresh = (self.token or {}).get("refresh_token")
		if not refresh:
			return False
		try:
			self.request_token({"grant_type": "refresh_token", "refresh_token": refresh})
			return True
		except FreesoundError:
			self.token = None
			return False

	def download_original(self, sound, destination, report_progress):
		"""Download a sound's original file to destination, calling report_progress(done, total)."""
		request = urllib.request.Request(
			DOWNLOAD_URL.format(sound_id=sound["id"]),
			headers={
				"Authorization": "Bearer " + self.token["access_token"],
				"User-Agent": USER_AGENT,
			},
		)
		partial = destination.with_name(destination.name + ".part")
		try:
			with open_url(request, timeout=60) as response, partial.open("wb") as file:
				total = int(response.headers.get("Content-Length") or sound.get("filesize") or 0)
				done = 0
				while True:
					chunk = response.read(65536)
					if not chunk:
						break
					file.write(chunk)
					done += len(chunk)
					report_progress(done, total)
		except urllib.error.HTTPError as error:
			partial.unlink(missing_ok=True)
			if error.code == 401:
				self.token = None
				raise FreesoundError("Freesound says the sign-in has run out. Please download again to sign in.")
			raise FreesoundError(describe_http_error(error))
		except (FreesoundError, OSError):
			partial.unlink(missing_ok=True)
			raise
		partial.replace(destination)


def write_credits(sound, sound_file, notes="", looked_up=False):
	"""Write the credit file, with any notes, beside a sound, and for a download add it to CREDITS.txt.

	looked_up marks a sound already on disk whose licence was looked up on Freesound."""
	credit_line = make_credit_line(sound)
	license_url = sound.get("license") or ""
	notes = " ".join(notes.split())
	credit_text = (
		f"{credit_line}\n\n"
		f"File: {sound_file.name}\n"
		f"Sound ID: {sound.get('id')}\n"
		f"Sound page: {sound_page(sound)}\n"
		f"Creator: {sound.get('username', 'unknown')}\n"
		f"License: {license_short_name(license_url)}, {license_url}\n"
		f"{'Looked up on Freesound' if looked_up else 'Downloaded'}: {time.strftime('%Y-%m-%d')}\n"
	)
	if notes:
		credit_text += f"Notes: {notes}\n"
	sound_file.with_suffix(".txt").write_text(credit_text, encoding="utf-8")
	if looked_up:
		return

	marker = f"[{sound.get('id')}]"
	credits_file = sound_file.parent / "CREDITS.txt"
	existing = credits_file.read_text(encoding="utf-8") if credits_file.exists() else ""
	if marker in existing:
		return
	with credits_file.open("a", encoding="utf-8") as file:
		if not existing:
			file.write("Freesound sound credits\n\n")
		file.write(f"{marker} {sound_file.name}\n{credit_line}\n\n")


class CallbackHandler(BaseHTTPRequestHandler):
	"""Catches Freesound's reply when it sends the browser back to the app after Authorize."""

	def do_GET(self):
		"""Read the code from the address and thank the user in the browser."""
		query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
		code = (query.get("code") or [""])[0]
		error = (query.get("error") or [""])[0]
		if code:
			message = "Signed in. You can close this tab and go back to Cerulean's Sound Searcher."
		elif error:
			message = f"Freesound refused the sign-in ({html.escape(error)}). Go back to Cerulean's Sound Searcher."
		else:
			message = "Freesound didn't send a code. Go back to Cerulean's Sound Searcher and try again."
		page = (
			"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
			f"<title>{APP_NAME}</title></head><body><h1>{message}</h1></body></html>"
		).encode("utf-8")
		self.send_response(200)
		self.send_header("Content-Type", "text/html; charset=utf-8")
		self.send_header("Content-Length", str(len(page)))
		self.end_headers()
		self.wfile.write(page)
		if code:
			self.server.on_code(code)
		elif error:
			self.server.on_error(error)

	def log_message(self, format, *args):
		"""Keep the server quiet."""


class SignInDialog(wx.Dialog):
	"""Explains signing in, opens the Authorize page and waits for the code."""

	def __init__(self, parent, client):
		"""Build the dialog and start listening for Freesound's reply."""
		super().__init__(parent, title="Sign in to Freesound")
		self.code = ""
		explanation = wx.StaticText(self, label=(
			"To download original files, Freesound needs you to allow this app once. "
			"Your browser is opening Freesound's Authorize page. Sign in there if asked, "
			"then press Authorize.\n\n"
			"If the browser comes back here by itself, this window closes. "
			"If Freesound shows you a code instead, paste it in the box below and press OK."
		))
		explanation.Wrap(460)
		code_label = wx.StaticText(self, label="&Code from Freesound:")
		self.code_box = wx.TextCtrl(self)
		open_button = wx.Button(self, label="Open the Authorize page &again")
		buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)

		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(explanation, 0, wx.ALL, 10)
		sizer.Add(code_label, 0, wx.LEFT | wx.RIGHT, 10)
		sizer.Add(self.code_box, 0, wx.EXPAND | wx.ALL, 10)
		sizer.Add(open_button, 0, wx.LEFT | wx.RIGHT, 10)
		sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 10)
		self.SetSizerAndFit(sizer)

		self.authorize_address = client.authorize_address()
		open_button.Bind(wx.EVT_BUTTON, lambda event: webbrowser.open(self.authorize_address))
		self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
		self.server = self.start_callback_server()
		self.code_box.SetFocus()
		webbrowser.open(self.authorize_address)

	def start_callback_server(self):
		"""Listen on localhost for the browser coming back; return None if the port is busy."""
		try:
			server = HTTPServer(("127.0.0.1", CALLBACK_PORT), CallbackHandler)
		except OSError:
			return None
		server.on_code = lambda code: wx.CallAfter(self.finish, code)
		server.on_error = lambda error: wx.CallAfter(self.refused, error)
		threading.Thread(target=server.serve_forever, daemon=True).start()
		return server

	def finish(self, code):
		"""Close the dialog with the code the browser brought back."""
		if self.IsModal():
			self.code = code
			self.EndModal(wx.ID_OK)

	def refused(self, error):
		"""Tell the user why Freesound sent the browser back without a code, and close."""
		if not self.IsModal():
			return
		if error == "access_denied":
			reason = "Freesound says access was denied. That happens when Deny is pressed on the Authorize page."
		else:
			reason = f"Freesound refused the sign-in. Its reason: {error}."
		wx.MessageBox(reason, APP_NAME, wx.OK | wx.ICON_WARNING, self)
		self.EndModal(wx.ID_CANCEL)

	def on_ok(self, event):
		"""Accept a pasted code."""
		code = self.code_box.GetValue().strip()
		if not code:
			wx.MessageBox("Paste the code from Freesound first.", APP_NAME, wx.OK, self)
			self.code_box.SetFocus()
			return
		self.code = code
		self.EndModal(wx.ID_OK)

	def Destroy(self):
		"""Stop the localhost listener and free its port when the dialog goes away."""
		if self.server:
			server = self.server
			self.server = None

			def close_server():
				"""Stop serving, then release the port."""
				server.shutdown()
				server.server_close()
			threading.Thread(target=close_server, daemon=True).start()
		return super().Destroy()


def count(number, word):
	"""Say a number with its word, singular for one: 1 sound, 2 sounds."""
	return f"{number} {word}" if number == 1 else f"{number} {word}s"


def load_credits_list():
	"""The credit lines gathered so far, kept between sessions in the settings folder."""
	try:
		return list(json.loads(CREDITS_LIST_FILE.read_text(encoding="utf-8")))
	except (OSError, json.JSONDecodeError, TypeError):
		return []


def save_credits_list(credits):
	"""Keep the credits list in the settings folder."""
	CREDITS_LIST_FILE.parent.mkdir(parents=True, exist_ok=True)
	CREDITS_LIST_FILE.write_text(json.dumps(credits, indent="\t"), encoding="utf-8")


def ask_library_name(parent, settings, suggestion, ignore=None):
	"""Ask for a library's short name, which must be unique and can't contain a slash; None if cancelled."""
	taken = {library["name"].lower() for library in settings["libraries"]} - {(ignore or "").lower()}
	while True:
		dialog = wx.TextEntryDialog(parent, "A short name for this library, shown with its sounds:", "Library name", value=suggestion)
		answer = dialog.ShowModal()
		name = " ".join(dialog.GetValue().split()).replace("/", " ").replace("\\", " ").strip()
		dialog.Destroy()
		if answer != wx.ID_OK:
			return None
		if not name:
			continue
		if name.lower() in taken:
			wx.MessageBox(f"There's already a library called {name}.", APP_NAME, wx.OK, parent)
			suggestion = name
			continue
		return name


def choose_library_folder(parent, settings):
	"""Ask for a folder of sounds and a name for it; return the new library, or None if cancelled or it overlaps another."""
	dialog = wx.DirDialog(parent, "Choose a folder of sounds")
	answer = dialog.ShowModal()
	folder = Path(dialog.GetPath()).resolve()
	dialog.Destroy()
	if answer != wx.ID_OK:
		return None
	for library in settings["libraries"]:
		existing = Path(library["folder"]).resolve()
		if folder == existing or existing in folder.parents or folder in existing.parents:
			wx.MessageBox(f"That folder overlaps the library {library['name']}, at {library['folder']}.", APP_NAME, wx.OK, parent)
			return None
	name = ask_library_name(parent, settings, folder.name)
	return {"name": name, "folder": str(folder)} if name else None


class TextViewDialog(wx.Dialog):
	"""Shows some text in a read-only box that a screen reader can review line by line."""

	def __init__(self, parent, title, label, text):
		"""Build the dialog with the text and a Close button."""
		super().__init__(parent, title=title, size=(640, 460), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
		text_label = wx.StaticText(self, label=label)
		box = wx.TextCtrl(self, value=text, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
		buttons = self.CreateStdDialogButtonSizer(wx.CLOSE)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(text_label, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
		sizer.Add(box, 1, wx.EXPAND | wx.ALL, 10)
		sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 10)
		self.SetSizer(sizer)
		self.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
		self.SetEscapeId(wx.ID_CLOSE)
		box.SetInsertionPoint(0)
		box.SetFocus()


class CredentialsDialog(wx.Dialog):
	"""The first-run step that asks for the user's own Freesound API credentials."""

	def __init__(self, parent, settings):
		"""Build the dialog, filled in with any credentials already saved."""
		super().__init__(parent, title="Freesound credentials")
		explanation = wx.StaticText(self, label=(
			"To search and download from Freesound, you need your own Freesound API credentials. They're free: "
			"press Get credentials, sign in to Freesound, and apply. For the callback address, enter "
			f"{CALLBACK_ADDRESS} so signing in finishes by itself. Then paste the client ID and client secret here.\n\n"
			"Press Skip to do this later in Settings. Searching your own library works without them."
		))
		explanation.Wrap(480)
		id_label = wx.StaticText(self, label="Client &ID:")
		self.id_box = wx.TextCtrl(self, value=settings["freesound_client_id"])
		secret_label = wx.StaticText(self, label="Client s&ecret:")
		self.secret_box = wx.TextCtrl(self, value=settings["freesound_client_secret"])
		get_button = wx.Button(self, label="&Get credentials")
		next_button = wx.Button(self, wx.ID_OK, label="&Next")
		skip_button = wx.Button(self, wx.ID_CANCEL, label="&Skip")
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		buttons.Add(next_button, 0, wx.RIGHT, 6)
		buttons.Add(skip_button, 0)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(explanation, 0, wx.ALL, 10)
		for control in (id_label, self.id_box, secret_label, self.secret_box, get_button):
			sizer.Add(control, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)
		sizer.Add(buttons, 0, wx.ALL, 10)
		self.SetSizerAndFit(sizer)
		get_button.Bind(wx.EVT_BUTTON, lambda event: webbrowser.open(app_settings.CREDENTIALS_PAGE))
		next_button.SetDefault()
		self.id_box.SetFocus()


class LicenceRuleDialog(wx.Dialog):
	"""Adds or edits one folder licence rule: where a folder's sounds came from and how they may be used."""

	def __init__(self, parent, settings, rule=None):
		"""Build the dialog, filled in from rule when editing one."""
		super().__init__(parent, title="Licence rule")
		self.settings = settings
		rule = rule or {"folder": "", "source": "", "licence": "", "cleared": "yes", "credit": ""}
		self.folder_key = rule["folder"]
		explanation = wx.StaticText(self, label=(
			"Sounds in this folder, and the folders inside it, get this licence, unless a sound has its own "
			"credit file or Freesound pack readme."
		))
		explanation.Wrap(460)
		folder_label = wx.StaticText(self, label="&Folder:")
		self.folder_box = wx.TextCtrl(self, value=self.folder_key, style=wx.TE_READONLY)
		choose_button = wx.Button(self, label="&Choose folder...")
		source_label = wx.StaticText(self, label="&Source, such as a website or pack:")
		self.source_box = wx.TextCtrl(self, value=rule["source"])
		licence_label = wx.StaticText(self, label="&Licence:")
		self.licence_box = wx.TextCtrl(self, value=rule["licence"])
		cleared_label = wx.StaticText(self, label="&Allowed in games you sell:")
		self.cleared_choice = wx.Choice(self, choices=[label for key, label in app_settings.CLEARED_CHOICES])
		self.cleared_choice.SetSelection([key for key, label in app_settings.CLEARED_CHOICES].index(rule["cleared"]))
		credit_label = wx.StaticText(self, label="C&redit line:")
		self.credit_box = wx.TextCtrl(self, value=rule.get("credit", ""))
		buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)

		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(explanation, 0, wx.ALL, 10)
		for label, control in (
			(folder_label, self.folder_box), (None, choose_button), (source_label, self.source_box),
			(licence_label, self.licence_box), (cleared_label, self.cleared_choice), (credit_label, self.credit_box),
		):
			if label:
				sizer.Add(label, 0, wx.LEFT | wx.RIGHT, 10)
			sizer.Add(control, 0, wx.EXPAND | wx.ALL, 10)
		sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 10)
		self.SetSizerAndFit(sizer)
		choose_button.Bind(wx.EVT_BUTTON, self.on_choose)
		self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
		(choose_button if not self.folder_key else self.source_box).SetFocus()

	def on_choose(self, event):
		"""Pick the folder, which must be one of the library folders or inside one."""
		start = app_settings.path_for(self.settings, self.folder_key) if self.folder_key else None
		dialog = wx.DirDialog(self, "Choose a folder inside one of your library folders", str(start or ""))
		if dialog.ShowModal() == wx.ID_OK:
			key = app_settings.key_for(self.settings, dialog.GetPath())
			if key is None:
				wx.MessageBox("That folder isn't inside any of your library folders.", APP_NAME, wx.OK, self)
			else:
				self.folder_key = key.rstrip("/")
				self.folder_box.SetValue(self.folder_key)
		dialog.Destroy()

	def on_ok(self, event):
		"""Check the rule has a folder and a licence before closing."""
		if not self.folder_key:
			wx.MessageBox("Choose the folder first.", APP_NAME, wx.OK, self)
			return
		if not self.licence_box.GetValue().strip():
			wx.MessageBox("Type the licence, for example CC0, CC BY 4.0, or the name of a store's licence.", APP_NAME, wx.OK, self)
			self.licence_box.SetFocus()
			return
		self.EndModal(wx.ID_OK)

	def result(self):
		"""The rule as entered."""
		return {
			"folder": self.folder_key,
			"source": self.source_box.GetValue().strip() or "Unknown",
			"licence": self.licence_box.GetValue().strip(),
			"cleared": app_settings.CLEARED_CHOICES[self.cleared_choice.GetSelection()][0],
			"credit": self.credit_box.GetValue().strip(),
		}


class SettingsDialog(wx.Dialog):
	"""The Settings window: Freesound credentials and filters, folders, and licence rules."""

	def __init__(self, parent, settings):
		"""Build the window from a copy of the settings, so Cancel changes nothing."""
		super().__init__(parent, title="Settings")
		self.settings = json.loads(json.dumps(settings))

		freesound_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Freesound")
		box = freesound_box.GetStaticBox()
		explanation = wx.StaticText(box, label=(
			"Your own Freesound API credentials, free from Freesound's API page. When you apply, set the callback "
			f"address to {CALLBACK_ADDRESS} and signing in finishes by itself. Searching your library works without them."
		))
		explanation.Wrap(520)
		id_label = wx.StaticText(box, label="Client &ID:")
		self.id_box = wx.TextCtrl(box, value=self.settings["freesound_client_id"])
		secret_label = wx.StaticText(box, label="Client s&ecret:")
		self.secret_box = wx.TextCtrl(box, value=self.settings["freesound_client_secret"])
		credentials_button = wx.Button(box, label="&Get credentials from Freesound")
		licences_label = wx.StaticText(box, label="Licences to search for:")
		self.licence_boxes = []
		for key, label, query in app_settings.LICENCE_CHOICES:
			check = wx.CheckBox(box, label=label)
			check.SetValue(key in self.settings["freesound_licences"])
			self.licence_boxes.append((key, check))
		channels_label = wx.StaticText(box, label="C&hannels:")
		self.channels_choice = self.make_choice(box, app_settings.CHANNEL_CHOICES, self.settings["freesound_channels"])
		rate_label = wx.StaticText(box, label="Sample &rate:")
		self.rate_choice = self.make_choice(box, app_settings.SAMPLE_RATE_CHOICES, self.settings["freesound_sample_rate"])
		for control in (explanation, id_label, self.id_box, secret_label, self.secret_box, credentials_button, licences_label):
			freesound_box.Add(control, 0, wx.EXPAND | wx.ALL, 4)
		for key, check in self.licence_boxes:
			freesound_box.Add(check, 0, wx.LEFT | wx.BOTTOM, 4)
		for label, control in ((channels_label, self.channels_choice), (rate_label, self.rate_choice)):
			freesound_box.Add(label, 0, wx.LEFT | wx.TOP, 4)
			freesound_box.Add(control, 0, wx.ALL, 4)

		folders_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Folders")
		box = folders_box.GetStaticBox()
		downloads_label = wx.StaticText(box, label="&Downloads folder:")
		self.downloads_box = wx.TextCtrl(box, value=self.settings["downloads_folder"])
		browse_button = wx.Button(box, label="&Browse...")
		libraries_label = wx.StaticText(box, label="&Library folders, searched with My library:")
		self.libraries_list = wx.ListBox(box, size=(-1, 90))
		add_library_button = wx.Button(box, label="&Add library folder...")
		rename_library_button = wx.Button(box, label="Re&name library...")
		remove_library_button = wx.Button(box, label="Re&move library")
		library_buttons = wx.BoxSizer(wx.HORIZONTAL)
		for button in (add_library_button, rename_library_button, remove_library_button):
			library_buttons.Add(button, 0, wx.RIGHT, 6)
		index_label = wx.StaticText(box, label="Inde&x file, which also holds your notes:")
		self.index_box = wx.TextCtrl(box, value=self.settings["index_file"], style=wx.TE_READONLY)
		choose_index_button = wx.Button(box, label="&Choose index...")
		new_index_button = wx.Button(box, label="Ne&w index...")
		backup_index_button = wx.Button(box, label="Back u&p index...")
		index_buttons = wx.BoxSizer(wx.HORIZONTAL)
		for button in (choose_index_button, new_index_button, backup_index_button):
			index_buttons.Add(button, 0, wx.RIGHT, 6)
		for control in (downloads_label, self.downloads_box, browse_button, libraries_label, self.libraries_list):
			folders_box.Add(control, 0, wx.EXPAND | wx.ALL, 4)
		folders_box.Add(library_buttons, 0, wx.ALL, 4)
		folders_box.Add(index_label, 0, wx.EXPAND | wx.ALL, 4)
		folders_box.Add(self.index_box, 0, wx.EXPAND | wx.ALL, 4)
		folders_box.Add(index_buttons, 0, wx.ALL, 4)
		self.original_index = self.settings["index_file"]

		rules_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Licence rules")
		box = rules_box.GetStaticBox()
		rules_explanation = wx.StaticText(box, label=(
			"Say where a folder's sounds came from and their licence, for sounds without their own credit file "
			"or Freesound readme. Index again after changing rules."
		))
		rules_explanation.Wrap(520)
		rules_label = wx.StaticText(box, label="Licence r&ules:")
		self.rules_list = wx.ListBox(box, size=(-1, 90))
		add_rule_button = wx.Button(box, label="Add rule...")
		edit_rule_button = wx.Button(box, label="Edit rule...")
		remove_rule_button = wx.Button(box, label="Remove rule")
		rule_buttons = wx.BoxSizer(wx.HORIZONTAL)
		for button in (add_rule_button, edit_rule_button, remove_rule_button):
			rule_buttons.Add(button, 0, wx.RIGHT, 6)
		for control in (rules_explanation, rules_label, self.rules_list):
			rules_box.Add(control, 0, wx.EXPAND | wx.ALL, 4)
		rules_box.Add(rule_buttons, 0, wx.ALL, 4)

		buttons = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
		sizer = wx.BoxSizer(wx.VERTICAL)
		for group in (freesound_box, folders_box, rules_box):
			sizer.Add(group, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)
		sizer.Add(buttons, 0, wx.EXPAND | wx.ALL, 10)
		self.SetSizerAndFit(sizer)

		self.fill_libraries()
		self.fill_rules()
		credentials_button.Bind(wx.EVT_BUTTON, lambda event: webbrowser.open(app_settings.CREDENTIALS_PAGE))
		browse_button.Bind(wx.EVT_BUTTON, self.on_browse_downloads)
		add_library_button.Bind(wx.EVT_BUTTON, self.on_add_library)
		rename_library_button.Bind(wx.EVT_BUTTON, self.on_rename_library)
		remove_library_button.Bind(wx.EVT_BUTTON, self.on_remove_library)
		choose_index_button.Bind(wx.EVT_BUTTON, self.on_choose_index)
		new_index_button.Bind(wx.EVT_BUTTON, self.on_new_index)
		backup_index_button.Bind(wx.EVT_BUTTON, self.on_backup_index)
		add_rule_button.Bind(wx.EVT_BUTTON, self.on_add_rule)
		edit_rule_button.Bind(wx.EVT_BUTTON, self.on_edit_rule)
		remove_rule_button.Bind(wx.EVT_BUTTON, self.on_remove_rule)
		self.rules_list.Bind(wx.EVT_LISTBOX_DCLICK, self.on_edit_rule)
		self.id_box.SetFocus()

	def make_choice(self, parent, choices, current):
		"""A drop-down of (key, label) choices with current selected."""
		choice = wx.Choice(parent, choices=[label for key, label in choices])
		keys = [key for key, label in choices]
		choice.SetSelection(keys.index(current) if current in keys else 0)
		return choice

	def fill_libraries(self, select=0):
		"""Show the library folders as "name: folder"."""
		self.libraries_list.Set([f"{library['name']}: {library['folder']}" for library in self.settings["libraries"]])
		if self.settings["libraries"]:
			self.libraries_list.SetSelection(min(select, len(self.settings["libraries"]) - 1))

	def fill_rules(self, select=0):
		"""Show the licence rules, most general folder first."""
		self.settings["licence_rules"].sort(key=lambda rule: rule["folder"].lower())
		labels = {key: label for key, label in app_settings.CLEARED_CHOICES}
		self.rules_list.Set([
			f"{rule['folder']}: {rule['licence']}, from {rule['source']}. Allowed in games you sell: {labels[rule['cleared']].split(',')[0]}"
			for rule in self.settings["licence_rules"]
		])
		if self.settings["licence_rules"]:
			self.rules_list.SetSelection(min(select, len(self.settings["licence_rules"]) - 1))

	def on_browse_downloads(self, event):
		"""Choose the downloads folder."""
		dialog = wx.DirDialog(self, "Choose the folder for downloads", self.downloads_box.GetValue())
		if dialog.ShowModal() == wx.ID_OK:
			self.downloads_box.SetValue(dialog.GetPath())
		dialog.Destroy()
		self.downloads_box.SetFocus()

	def on_add_library(self, event):
		"""Add a folder of sounds to the library."""
		library = choose_library_folder(self, self.settings)
		if library:
			self.settings["libraries"].append(library)
			self.fill_libraries(len(self.settings["libraries"]) - 1)
		self.libraries_list.SetFocus()

	def on_choose_index(self, event):
		"""Use an existing index file."""
		dialog = wx.FileDialog(
			self, "Choose an index", str(Path(self.settings["index_file"]).parent), wildcard="Sound index (*.db)|*.db",
			style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
		)
		if dialog.ShowModal() == wx.ID_OK:
			import sound_index
			try:
				sound_index.check_index_file(dialog.GetPath())
			except RuntimeError as error:
				wx.MessageBox(str(error), APP_NAME, wx.OK | wx.ICON_WARNING, self)
			else:
				self.settings["index_file"] = dialog.GetPath()
				self.index_box.SetValue(dialog.GetPath())
		dialog.Destroy()
		self.index_box.SetFocus()

	def on_new_index(self, event):
		"""Start a new, empty index in a file of the user's choosing; it's filled the next time the index is updated."""
		dialog = wx.FileDialog(
			self, "Name the new index", str(Path(self.settings["index_file"]).parent), "sound_index.db",
			wildcard="Sound index (*.db)|*.db", style=wx.FD_SAVE,
		)
		if dialog.ShowModal() == wx.ID_OK:
			path = Path(dialog.GetPath())
			if path.exists():
				wx.MessageBox(f"{path.name} already exists. Use Choose index to use it, or pick a new name.", APP_NAME, wx.OK, self)
			else:
				self.settings["index_file"] = str(path)
				self.index_box.SetValue(str(path))
				wx.MessageBox("The new index is empty. Update the library index (Ctrl+U) to fill it.", APP_NAME, wx.OK, self)
		dialog.Destroy()
		self.index_box.SetFocus()

	def on_backup_index(self, event):
		"""Copy the index in use to a file of the user's choosing."""
		current = Path(self.original_index)
		if not current.exists():
			wx.MessageBox("There's no index to back up yet.", APP_NAME, wx.OK, self)
			return
		dialog = wx.FileDialog(
			self, "Back up the index to", str(current.parent), f"sound_index backup {time.strftime('%Y-%m-%d')}.db",
			wildcard="Sound index (*.db)|*.db", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
		)
		if dialog.ShowModal() == wx.ID_OK:
			import sound_index
			destination = Path(dialog.GetPath())
			try:
				destination.unlink(missing_ok=True)
				sound_index.backup_index(dict(self.settings, index_file=str(current)), destination)
				wx.MessageBox(f"Backed up to {destination.name}.", APP_NAME, wx.OK, self)
			except Exception as error:
				wx.MessageBox(f"The backup didn't work: {error}", APP_NAME, wx.OK | wx.ICON_WARNING, self)
		dialog.Destroy()
		self.index_box.SetFocus()

	def on_rename_library(self, event):
		"""Give the selected library a new short name."""
		index = self.libraries_list.GetSelection()
		if index == wx.NOT_FOUND:
			return
		library = self.settings["libraries"][index]
		name = ask_library_name(self, self.settings, library["name"], ignore=library["name"])
		if name and name != library["name"]:
			for rule in self.settings["licence_rules"]:
				if rule["folder"] == library["name"] or rule["folder"].startswith(library["name"] + "/"):
					rule["folder"] = name + rule["folder"][len(library["name"]):]
			library["name"] = name
			self.fill_libraries(index)
			self.fill_rules()
		self.libraries_list.SetFocus()

	def on_remove_library(self, event):
		"""Stop searching the selected library folder; its files aren't touched."""
		index = self.libraries_list.GetSelection()
		if index == wx.NOT_FOUND:
			return
		library = self.settings["libraries"][index]
		answer = wx.MessageBox(
			f"Remove the library {library['name']}? Its sounds leave the index the next time you index, "
			"but the files and your notes stay.",
			APP_NAME, wx.YES_NO, self,
		)
		if answer == wx.YES:
			del self.settings["libraries"][index]
			self.fill_libraries(index)
		self.libraries_list.SetFocus()

	def on_add_rule(self, event):
		"""Add a licence rule for a folder."""
		if not self.settings["libraries"]:
			wx.MessageBox("Add a library folder first.", APP_NAME, wx.OK, self)
			return
		dialog = LicenceRuleDialog(self, self.settings)
		if dialog.ShowModal() == wx.ID_OK:
			rule = dialog.result()
			self.settings["licence_rules"] = [old for old in self.settings["licence_rules"] if old["folder"] != rule["folder"]]
			self.settings["licence_rules"].append(rule)
			self.fill_rules()
			self.rules_list.SetSelection(self.settings["licence_rules"].index(rule))
		dialog.Destroy()
		self.rules_list.SetFocus()

	def on_edit_rule(self, event):
		"""Change the selected licence rule."""
		index = self.rules_list.GetSelection()
		if index == wx.NOT_FOUND:
			return
		dialog = LicenceRuleDialog(self, self.settings, self.settings["licence_rules"][index])
		if dialog.ShowModal() == wx.ID_OK:
			self.settings["licence_rules"][index] = dialog.result()
			self.fill_rules(index)
		dialog.Destroy()
		self.rules_list.SetFocus()

	def on_remove_rule(self, event):
		"""Delete the selected licence rule."""
		index = self.rules_list.GetSelection()
		if index != wx.NOT_FOUND:
			del self.settings["licence_rules"][index]
			self.fill_rules(index)
		self.rules_list.SetFocus()

	def result(self):
		"""The settings as changed in the window."""
		self.settings["freesound_client_id"] = self.id_box.GetValue().strip()
		self.settings["freesound_client_secret"] = self.secret_box.GetValue().strip()
		self.settings["freesound_licences"] = [key for key, check in self.licence_boxes if check.GetValue()] or ["cc0"]
		self.settings["freesound_channels"] = app_settings.CHANNEL_CHOICES[self.channels_choice.GetSelection()][0]
		self.settings["freesound_sample_rate"] = app_settings.SAMPLE_RATE_CHOICES[self.rate_choice.GetSelection()][0]
		self.settings["downloads_folder"] = self.downloads_box.GetValue().strip() or app_settings.default_settings()["downloads_folder"]
		return self.settings


class MainWindow(wx.Frame):
	"""The search window: search box, filters, results list and details."""

	def __init__(self, settings, client):
		"""Build the window, menus and keys."""
		super().__init__(None, title=APP_NAME, size=(800, 600))
		self.Centre()
		self.settings = settings
		self.client = client
		self.sounds = []
		self.query = ""
		self.page = 1
		self.total = 0
		self.more_available = False
		self.busy = False
		self.preview_cache = {}
		self.playing_id = None
		self.downloaded_ids = self.read_downloaded_ids()
		self.sound_index = None
		self.clap_model = None
		self.library_request = None
		self.library_limit = LIBRARY_PAGE_SIZE
		self.indexing = False
		self.stop_indexing = False
		self.lookup_running = False
		self.stop_lookup = False
		self.downloading_model = False
		self.stop_download = False
		self.credits_list = load_credits_list()

		panel = wx.Panel(self)
		search_label = wx.StaticText(panel, label="&Search for:")
		self.search_box = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
		search_button = wx.Button(panel, label="Search")
		place_label = wx.StaticText(panel, label="Searc&h in:")
		self.place_choice = wx.Choice(panel, choices=SEARCH_PLACES)
		self.place_choice.SetSelection(0)
		library_label = wx.StaticText(panel, label="Librar&y:")
		self.library_choice = wx.Choice(panel)
		self.cleared_box = wx.CheckBox(panel, label="&Cleared licences only")
		self.cleared_box.SetValue(True)
		length_label = wx.StaticText(panel, label="&Length:")
		self.length_choice = wx.Choice(panel, choices=[label for label, value in DURATION_CHOICES])
		self.length_choice.SetSelection(0)
		sort_label = wx.StaticText(panel, label="So&rt by:")
		self.sort_choice = wx.Choice(panel, choices=[label for label, value in SORT_CHOICES])
		self.sort_choice.SetSelection(0)
		self.results_label = wx.StaticText(panel, label="Res&ults:")
		self.results_list = wx.ListBox(panel, style=wx.LB_SINGLE)
		details_label = wx.StaticText(panel, label="&Details:")
		self.details_box = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
		self.play_button = wx.Button(panel, label="&Play preview")
		self.download_button = wx.Button(panel, label="Do&wnload")
		self.similar_button = wx.Button(panel, label="Find s&imilar")
		self.notes_button = wx.Button(panel, label="Edit &notes")
		self.more_button = wx.Button(panel, label="&More results")
		self.more_button.Disable()

		search_row = wx.BoxSizer(wx.HORIZONTAL)
		search_row.Add(search_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
		search_row.Add(self.search_box, 1, wx.RIGHT, 6)
		search_row.Add(search_button, 0)
		filter_row = wx.BoxSizer(wx.HORIZONTAL)
		filter_row.Add(place_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
		filter_row.Add(self.place_choice, 0, wx.RIGHT, 18)
		filter_row.Add(library_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
		filter_row.Add(self.library_choice, 0, wx.RIGHT, 18)
		filter_row.Add(length_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
		filter_row.Add(self.length_choice, 0, wx.RIGHT, 18)
		filter_row.Add(sort_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
		filter_row.Add(self.sort_choice, 0, wx.RIGHT, 18)
		filter_row.Add(self.cleared_box, 0, wx.ALIGN_CENTER_VERTICAL)
		button_row = wx.BoxSizer(wx.HORIZONTAL)
		button_row.Add(self.play_button, 0, wx.RIGHT, 6)
		button_row.Add(self.download_button, 0, wx.RIGHT, 6)
		button_row.Add(self.similar_button, 0, wx.RIGHT, 6)
		button_row.Add(self.notes_button, 0, wx.RIGHT, 6)
		button_row.Add(self.more_button, 0)

		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(search_row, 0, wx.EXPAND | wx.ALL, 10)
		sizer.Add(filter_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		sizer.Add(self.results_label, 0, wx.LEFT | wx.RIGHT, 10)
		sizer.Add(self.results_list, 3, wx.EXPAND | wx.ALL, 10)
		sizer.Add(details_label, 0, wx.LEFT | wx.RIGHT, 10)
		sizer.Add(self.details_box, 2, wx.EXPAND | wx.ALL, 10)
		sizer.Add(button_row, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
		panel.SetSizer(sizer)
		self.panel = panel

		self.CreateStatusBar()
		self.build_menus()
		self.fill_library_choice()
		self.show_place_controls()
		self.update_buttons()

		self.search_box.Bind(wx.EVT_TEXT_ENTER, self.on_search)
		search_button.Bind(wx.EVT_BUTTON, self.on_search)
		self.results_list.Bind(wx.EVT_LISTBOX, self.on_select)
		self.results_list.Bind(wx.EVT_KEY_DOWN, self.on_list_key)
		self.play_button.Bind(wx.EVT_BUTTON, self.on_play)
		self.download_button.Bind(wx.EVT_BUTTON, self.on_download)
		self.similar_button.Bind(wx.EVT_BUTTON, self.on_similar)
		self.notes_button.Bind(wx.EVT_BUTTON, self.on_edit_notes)
		self.place_choice.Bind(wx.EVT_CHOICE, self.on_place_changed)
		self.more_button.Bind(wx.EVT_BUTTON, self.on_more)
		self.Bind(wx.EVT_CHAR_HOOK, self.on_char_hook)
		self.Bind(wx.EVT_CLOSE, self.on_close)

		self.set_status("Type what you're looking for and press Enter.", spoken=False)
		self.search_box.SetFocus()
		if self.settings["libraries"] and Path(self.settings["index_file"]).exists():
			wx.CallLater(3000, self.check_for_new_sounds)

	def build_menus(self):
		"""Create the File, Sound and Help menus with their shortcut keys."""
		menu_bar = wx.MenuBar()
		file_menu = wx.Menu()
		self.add_menu_item(file_menu, "&Find\tCtrl+F", lambda event: self.search_box.SetFocus())
		self.add_menu_item(file_menu, "Search F&reesound\tCtrl+1", lambda event: self.choose_place(0))
		self.add_menu_item(file_menu, "Search my &library\tCtrl+2", lambda event: self.choose_place(1))
		self.add_menu_item(file_menu, "Open &downloads folder\tCtrl+L", self.on_open_downloads)
		self.add_menu_item(file_menu, "Move &all downloads to library...", self.on_move_downloads)
		file_menu.AppendSeparator()
		self.add_menu_item(file_menu, "&Update library index\tCtrl+U", self.on_update_index)
		self.add_menu_item(file_menu, "Open index re&port", self.on_open_report)
		self.add_menu_item(file_menu, "Look up licences &on Freesound...", self.on_lookup_licences)
		file_menu.AppendSeparator()
		self.add_menu_item(file_menu, "Show &credits list", self.on_show_credits)
		self.add_menu_item(file_menu, "Sa&ve credits list...", self.on_save_credits)
		self.add_menu_item(file_menu, "Cl&ear credits list", self.on_clear_credits)
		file_menu.AppendSeparator()
		self.add_menu_item(file_menu, "&Sign in to Freesound", self.on_sign_in)
		self.add_menu_item(file_menu, "Se&ttings\tF2", self.on_settings)
		self.add_menu_item(file_menu, "First-ti&me setup...", lambda event: self.run_first_setup())
		file_menu.AppendSeparator()
		self.add_menu_item(file_menu, "E&xit", lambda event: self.Close(), wx.ID_EXIT)
		sound_menu = wx.Menu()
		self.add_menu_item(sound_menu, "&Download\tCtrl+D", self.on_download)
		self.add_menu_item(sound_menu, "Find s&imilar\tCtrl+M", self.on_similar)
		self.add_menu_item(sound_menu, "Edit &notes\tCtrl+E", self.on_edit_notes)
		self.add_menu_item(sound_menu, "&More results\tCtrl+N", self.on_more)
		self.add_menu_item(sound_menu, "&Open Freesound page or folder\tCtrl+O", self.on_open_page)
		self.add_menu_item(sound_menu, "&Copy credit line\tCtrl+Shift+C", self.on_copy_credit)
		self.add_menu_item(sound_menu, "Add to credits lis&t\tCtrl+K", self.on_add_credit)
		self.add_menu_item(sound_menu, "Add download to &library...\tCtrl+Shift+A", self.on_add_to_library)
		help_menu = wx.Menu()
		self.add_menu_item(help_menu, "&Keys\tF1", lambda event: wx.MessageBox(KEYS_HELP, "Keys", wx.OK, self))
		self.add_menu_item(help_menu, "&About", self.on_about)
		menu_bar.Append(file_menu, "&File")
		menu_bar.Append(sound_menu, "S&ound")
		menu_bar.Append(help_menu, "&Help")
		self.SetMenuBar(menu_bar)

	def add_menu_item(self, menu, label, handler, item_id=wx.ID_ANY):
		"""Add one menu item and connect it to its handler."""
		item = menu.Append(item_id, label)
		self.Bind(wx.EVT_MENU, handler, item)

	def set_status(self, text, spoken=True):
		"""Show a message in the status bar and, unless spoken is False, say it."""
		self.SetStatusText(text)
		if spoken:
			speak(text)

	def selected_sound(self):
		"""Return the sound selected in the results list, or None."""
		index = self.results_list.GetSelection()
		if index == wx.NOT_FOUND or index >= len(self.sounds):
			return None
		return self.sounds[index]

	def update_buttons(self):
		"""Enable or disable the buttons to match the selection and whether a job is running."""
		has_sound = self.selected_sound() is not None
		self.play_button.Enable(has_sound)
		self.download_button.Enable(has_sound and not self.busy)
		self.similar_button.Enable(has_sound and not self.busy)
		self.notes_button.Enable(has_sound and not self.busy)
		self.more_button.Enable(self.more_available and not self.busy)

	def library_mode(self):
		"""True when searching the sound archive instead of Freesound."""
		return self.place_choice.GetSelection() == 1

	def show_place_controls(self):
		"""Show the buttons and filters that fit the place being searched."""
		library = self.library_mode()
		self.download_button.Show(not library)
		self.similar_button.Show(library)
		self.notes_button.Show(library)
		self.sort_choice.Enable(not library)
		self.cleared_box.Enable(library)
		self.library_choice.Enable(library)
		self.panel.Layout()

	def fill_library_choice(self):
		"""List All libraries and each library by name in the Library box, keeping the choice if it still exists."""
		current = self.library_choice.GetStringSelection()
		names = [ALL_LIBRARIES] + [library["name"] for library in self.settings["libraries"]]
		self.library_choice.Set(names)
		self.library_choice.SetSelection(names.index(current) if current in names else 0)

	def chosen_library(self):
		"""The library name chosen in the Library box, or None for all of them."""
		index = self.library_choice.GetSelection()
		return None if index <= 0 else self.library_choice.GetString(index)

	def choose_place(self, place):
		"""Switch between searching Freesound and the library from the menu or keys."""
		if self.busy:
			return
		self.place_choice.SetSelection(place)
		self.on_place_changed(None)
		self.search_box.SetFocus()

	def on_place_changed(self, event):
		"""Clear the results when switching places, and say where searches now go."""
		self.stop_preview()
		self.sounds = []
		self.library_request = None
		self.more_available = False
		self.results_list.Clear()
		self.details_box.Clear()
		self.results_label.SetLabel("Res&ults:")
		self.show_place_controls()
		self.update_buttons()
		self.set_status(f"Searching {SEARCH_PLACES[self.place_choice.GetSelection()]}.")

	def sound_key(self, sound):
		"""A Freesound sound's ID, or a library sound's path, to tell sounds apart."""
		return sound.get("id") or sound.get("path")

	def list_line(self, sound):
		"""Return a results line, marked when a Freesound sound is already downloaded."""
		if "path" in sound:
			return make_library_line(sound)
		line = make_list_line(sound)
		if sound.get("id") in self.downloaded_ids:
			line += ", downloaded"
		return line

	def downloads_folder(self):
		"""The folder downloads go in, from the settings."""
		return Path(self.settings["downloads_folder"])

	def read_downloaded_ids(self):
		"""Find the Freesound IDs already downloaded, from the downloads folder and the library index."""
		ids = set()
		folder = self.downloads_folder()
		if folder.is_dir():
			for path in folder.iterdir():
				match = re.match(r"(\d+) ", path.name)
				if match:
					ids.add(int(match.group(1)))
		if Path(self.settings["index_file"]).exists():
			try:
				import sound_index
				connection = sound_index.open_database(self.settings)
				for (name,) in connection.execute("select name from sounds"):
					match = re.match(r"(\d+) ", name or "")
					if match:
						ids.add(int(match.group(1)))
				connection.close()
			except Exception:
				pass
		return ids

	def lookup_candidates(self):
		"""Library sounds with an unknown licence whose file names start with a Freesound number and that have no credit file."""
		import sound_index
		connection = sound_index.open_database(self.settings)
		candidates = []
		for (key,) in connection.execute("select path from sounds where cleared = 'unknown' order by path"):
			path = app_settings.path_for(self.settings, key)
			match = FREESOUND_NUMBER.match(path.name) if path else None
			if match and path.exists() and not path.with_suffix(".txt").exists():
				candidates.append((key, path, int(match.group(1))))
		connection.close()
		return candidates

	def on_lookup_licences(self, event):
		"""Look up the licences of library sounds named with Freesound numbers, writing a credit file beside each."""
		if self.lookup_running:
			if wx.MessageBox("Stop looking up licences? What's been found so far is kept.", APP_NAME, wx.YES_NO, self) == wx.YES:
				self.stop_lookup = True
			return
		if not self.client.api_key:
			wx.MessageBox("Looking up licences needs your own Freesound API credentials. Add them in Settings (F2).", APP_NAME, wx.OK, self)
			return
		if not Path(self.settings["index_file"]).exists():
			wx.MessageBox("Index your library first, with Update library index (Ctrl+U).", APP_NAME, wx.OK, self)
			return
		candidates = self.lookup_candidates()
		if not candidates:
			wx.MessageBox("There are no library sounds with an unknown licence and a Freesound number in their name.", APP_NAME, wx.OK, self)
			return
		minutes = max(1, round(len(candidates) * LOOKUP_GAP_SECONDS / 60))
		answer = wx.MessageBox(
			f"{len(candidates)} library sounds have an unknown licence and a Freesound number in their file name. "
			f"Look them up on Freesound? It takes about {minutes} minutes, and a credit file is written beside each sound found. "
			"You can keep searching meanwhile, and choose this menu item again to stop.",
			APP_NAME, wx.YES_NO, self,
		)
		if answer != wx.YES:
			return
		self.lookup_running = True
		self.stop_lookup = False
		self.set_status(f"Looking up {len(candidates)} licences on Freesound...")
		threading.Thread(target=self.lookup_licences, args=(candidates,), daemon=True).start()

	def lookup_licences(self, candidates):
		"""The lookup itself, on its own thread: ask Freesound about each sound, write credit files, update the index."""
		import sound_index
		connection = sound_index.open_database(self.settings)
		licences = sound_index.LicenceFinder(self.settings)
		found, gone, failed = [], [], []
		failures_in_a_row = 0
		for number, (key, path, sound_id) in enumerate(candidates, 1):
			if self.stop_lookup or failures_in_a_row >= 5:
				break
			for attempt in range(3):
				try:
					info = self.client.sound_info(sound_id)
					break
				except FreesoundError as error:
					if str(error) == "busy" and attempt < 2:
						wx.CallAfter(self.set_status, "Freesound asked to slow down. Waiting a minute...", False)
						time.sleep(60)
						continue
					info = error
					break
			if isinstance(info, FreesoundError):
				failed.append(f"{key}: {info}")
				failures_in_a_row += 1
			elif info is None:
				gone.append(key)
				failures_in_a_row = 0
			else:
				try:
					write_credits(info, path, looked_up=True)
					source, licence, cleared, credit = licences.from_credit_file(path)
					connection.execute(
						"update sounds set source = ?, licence = ?, cleared = ?, credit = ? where path = ?",
						(source, licence, cleared, credit, key),
					)
					found.append(f"{key}: {licence}")
					failures_in_a_row = 0
				except OSError as error:
					failed.append(f"{key}: couldn't write the credit file ({error})")
			if number % 25 == 0:
				connection.commit()
				wx.CallAfter(self.set_status, f"Looked up {number} of {len(candidates)} licences.", number % 100 == 0)
			time.sleep(LOOKUP_GAP_SECONDS)
		connection.commit()
		connection.close()
		report = Path(self.settings["index_file"]).parent / "licence lookup report.txt"
		lines = [
			f"Freesound licence lookup, {time.strftime('%d %B %Y, %H:%M')}",
			"",
			f"Sounds to look up: {len(candidates)}. Found: {len(found)}. No longer on Freesound: {len(gone)}. Failed: {len(failed)}.",
		]
		if self.stop_lookup:
			lines.append("Stopped before the end. Choose Look up licences again to carry on.")
		elif failures_in_a_row >= 5:
			lines.append("Stopped after five failures in a row. Check the connection and your credentials, then look up again.")
		for title, items in (("Found", found), ("No longer on Freesound (licence still unknown)", gone), ("Failed", failed)):
			if items:
				lines += ["", title + ":"] + [f"\t{item}" for item in items]
		report.write_text("\n".join(lines) + "\n", encoding="utf-8")
		wx.CallAfter(self.lookup_finished, len(found), len(gone), len(failed), report)

	def lookup_finished(self, found, gone, failed, report):
		"""Say how the licence lookup went."""
		self.lookup_running = False
		message = (
			f"Licence lookup finished. Found {found}, no longer on Freesound {gone}, failed {failed}. "
			f"The list is in {report.name}, beside the index."
		)
		self.set_status(message)

	def on_settings(self, event):
		"""Open the Settings window and put any changes into effect."""
		if self.busy:
			return
		if self.indexing or self.lookup_running:
			self.set_status("Settings can't be changed while the index is updating or licences are being looked up.")
			return
		old_client_id = self.client.client_id
		old_libraries = {library["folder"]: library["name"] for library in self.settings["libraries"]}
		dialog = SettingsDialog(self, self.settings)
		if dialog.ShowModal() == wx.ID_OK:
			new_settings = dialog.result()
			renamed = [
				(old_libraries[library["folder"]], library["name"]) for library in new_settings["libraries"]
				if library["folder"] in old_libraries and old_libraries[library["folder"]] != library["name"]
			]
			if renamed and Path(new_settings["index_file"]).exists():
				import sound_index
				for old_name, new_name in renamed:
					sound_index.rename_library(new_settings, old_name, new_name)
			self.settings.clear()
			self.settings.update(new_settings)
			app_settings.save(self.settings)
			if self.client.client_id != old_client_id:
				self.client.forget_sign_in()
			self.sound_index = None
			self.downloaded_ids = self.read_downloaded_ids()
			self.fill_library_choice()
			added = [library for library in new_settings["libraries"] if library["folder"] not in old_libraries]
			dialog.Destroy()
			if added and wx.MessageBox(
				"Index the new library folders now? You can keep searching while it runs.", APP_NAME, wx.YES_NO, self,
			) == wx.YES:
				self.on_update_index(None)
			else:
				self.set_status("Settings saved." + (" Update the library index (Ctrl+U) to add the new folders." if added else ""))
			return
		dialog.Destroy()

	def on_update_index(self, event):
		"""Update the library index in the background, or offer to stop an update that's running."""
		if self.indexing:
			if wx.MessageBox("Stop updating the index? What's done so far is kept.", APP_NAME, wx.YES_NO, self) == wx.YES:
				self.stop_indexing = True
				self.set_status("Stopping after the current sound...")
			return
		if not self.settings["libraries"]:
			wx.MessageBox("Add your sound folders in Settings (F2) first.", APP_NAME, wx.OK, self)
			return
		self.start_indexing()

	def start_indexing(self, only=None, finished=None):
		"""Index the library (or one folder of it) on another thread, saying how it's going; then call finished()."""
		import sound_index
		if not sound_index.model_ready():
			self.ensure_model_then(lambda: self.start_indexing(only, finished))
			return
		self.indexing = True
		self.stop_indexing = False
		self.set_status("Updating the library index...")
		progress = {"last spoken": 0.0}

		def report(text):
			"""Show each progress message, and say one about once a minute."""
			now = time.time()
			spoken = now - progress["last spoken"] > 60 or "to index" in text
			if spoken:
				progress["last spoken"] = now
			wx.CallAfter(self.set_status, text, spoken)

		def work():
			"""Run the indexer and pass its outcome back."""
			try:
				outcome = sound_index.build_index(
					self.settings, only=only, report=report, should_stop=lambda: self.stop_indexing, model=self.clap_model,
				)
			except Exception as error:
				outcome = error
			wx.CallAfter(self.indexing_finished, outcome, only, finished)

		threading.Thread(target=work, daemon=True).start()

	def ensure_model_then(self, action):
		"""Do action() once the sound model is downloaded, offering to download it first if it isn't."""
		import model_download
		import sound_index
		if sound_index.model_ready():
			action()
			return
		if self.downloading_model:
			if wx.MessageBox("Stop downloading the sound model? You can download it later.", APP_NAME, wx.YES_NO, self) == wx.YES:
				self.stop_download = True
			return
		answer = wx.MessageBox(
			f"Searching and indexing your library need the sound model, a one-time download of {model_download.download_size_text()}. "
			"It's kept in your AppData folder. Download it now? You can search Freesound meanwhile.",
			APP_NAME, wx.YES_NO, self,
		)
		if answer != wx.YES:
			self.set_status("The sound model wasn't downloaded. Library search and indexing need it.")
			return
		self.downloading_model = True
		self.stop_download = False
		progress = {"last spoken": 0.0}

		def report(text):
			"""Show each progress message, and say one about every 30 seconds."""
			now = time.time()
			spoken = now - progress["last spoken"] > 30 or "ready" in text
			if spoken:
				progress["last spoken"] = now
			wx.CallAfter(self.set_status, text, spoken)

		def work():
			"""Download the model and pass back any error."""
			try:
				model_download.download_model(app_settings.MODEL_FOLDER, report, lambda: self.stop_download)
				outcome = None
			except Exception as error:
				outcome = error
			wx.CallAfter(self.model_downloaded, outcome, action)

		threading.Thread(target=work, daemon=True).start()

	def model_downloaded(self, outcome, action):
		"""Carry on with what needed the model, or say why the download didn't finish."""
		import model_download
		self.downloading_model = False
		if isinstance(outcome, model_download.DownloadStopped):
			self.set_status("The sound model download was stopped. What was downloaded is kept for next time.")
		elif outcome is not None:
			message = f"The sound model didn't download: {outcome}. Check your internet connection and try again."
			self.set_status(message, spoken=False)
			wx.MessageBox(message, APP_NAME, wx.OK | wx.ICON_WARNING, self)
		else:
			action()

	def indexing_finished(self, outcome, only, finished):
		"""Say how the index update went."""
		self.indexing = False
		if isinstance(outcome, Exception):
			self.set_status(f"Updating the index didn't work: {outcome}")
			wx.MessageBox(f"Updating the index didn't work: {outcome}", APP_NAME, wx.OK | wx.ICON_WARNING, self)
			return
		indexed, errors, stopped = outcome
		self.downloaded_ids = self.read_downloaded_ids()
		if only:
			message = f"Added to the library and indexed: {count(indexed, 'sound')}."
		elif stopped:
			message = f"Index update stopped after {count(indexed, 'sound')}. Press Ctrl+U to carry on."
		else:
			message = f"Index updated: {count(indexed, 'sound')} indexed."
		if errors:
			message += f" {len(errors)} couldn't be read; they're listed in the index report."
		self.set_status(message)
		if finished:
			finished()

	def on_open_report(self, event):
		"""Open the report from the last index update."""
		import sound_index
		report = sound_index.report_file(self.settings)
		if report.exists():
			wx.LaunchDefaultApplication(str(report))
		else:
			self.set_status("There's no index report yet. Update the library index (Ctrl+U) to make one.")

	def check_for_new_sounds(self):
		"""Look, in the background, for library sounds added, changed or removed since the last index update."""
		import sound_index
		if self.indexing:
			return

		def work():
			"""Count the changes."""
			return sound_index.pending_changes(self.settings)

		def done(counts):
			"""Mention the changes, if there are any."""
			changed, gone = counts
			if (changed or gone) and not self.indexing:
				parts = []
				if changed:
					parts.append(f"{count(changed, 'new or changed sound')}")
				if gone:
					parts.append(f"{gone} removed")
				self.set_status(f"Your library has {' and '.join(parts)} since it was indexed. Press Ctrl+U to update the index.")

		self.run_in_background(work, done, lambda message: None)

	def run_in_background(self, work, on_done, on_error):
		"""Run work() on another thread, then call on_done(result) or on_error(message) on this one."""
		def runner():
			"""Do the work and report back to the window."""
			try:
				result = work()
			except FreesoundError as error:
				wx.CallAfter(on_error, str(error))
			except Exception as error:
				wx.CallAfter(on_error, f"Something went wrong: {error}")
			else:
				wx.CallAfter(on_done, result)
		threading.Thread(target=runner, daemon=True).start()

	def on_search(self, event):
		"""Start a new search from the search box."""
		query = self.search_box.GetValue().strip()
		if not query:
			self.set_status("Type something to search for first.")
			return
		if self.busy:
			return
		self.query = query
		self.page = 1
		self.library_request = None
		self.sounds = []
		self.results_list.Clear()
		self.details_box.Clear()
		if self.library_mode():
			self.library_request = ("search", query)
			self.library_limit = LIBRARY_PAGE_SIZE
			self.ensure_model_then(self.fetch_library)
		else:
			self.fetch_page()

	def on_more(self, event):
		"""Load the next page of results."""
		if self.busy or not self.more_available:
			return
		if self.library_request:
			self.library_limit += LIBRARY_PAGE_SIZE
			self.fetch_library()
			return
		self.page += 1
		self.fetch_page()

	def fetch_library(self):
		"""Search the library in the background, loading the index and the sound model the first time."""
		import sound_index
		self.busy = True
		self.update_buttons()
		kind, value = self.library_request
		if self.sound_index is None:
			self.set_status("Loading the sound library. The first search takes a few seconds.")
		elif kind == "search":
			self.set_status(f"Searching the library for {value}...")
		else:
			self.set_status("Finding similar sounds...")
		limit = self.library_limit
		cleared_only = self.cleared_box.GetValue()
		max_duration = DURATION_CHOICES[self.length_choice.GetSelection()][1]
		library = self.chosen_library()

		def work():
			"""Load or refresh the index if needed, then search it."""
			if self.sound_index is None or self.sound_index.is_stale():
				self.sound_index = sound_index.SoundIndex(self.settings, self.clap_model)
			if not self.sound_index.paths:
				if not self.settings["libraries"]:
					raise FreesoundError("No library folders yet. Add your sound folders in Settings (F2), then update the library index (Ctrl+U).")
				raise FreesoundError("The library index is empty. Update it with Ctrl+U first.")
			try:
				if kind == "search":
					return self.sound_index.search(value, limit, cleared_only, max_duration, library)
				return self.sound_index.similar(value, limit, cleared_only, max_duration, library)
			finally:
				self.clap_model = self.sound_index.model

		self.run_in_background(work, lambda results: self.show_library_results(results, limit), self.search_failed)

	def show_library_results(self, results, limit):
		"""Add the newly found library sounds to the list and move focus to the first new one."""
		self.busy = False
		self.more_available = len(results) >= limit
		first_new = len(self.sounds)
		for score, details in results[first_new:]:
			self.sounds.append(details)
			self.results_list.Append(self.list_line(details))
		kind, value = self.library_request
		about = value if kind == "search" else "sounds like " + Path(value).stem
		summary = f"{len(self.sounds)} library sounds"
		self.results_label.SetLabel(f"Res&ults, {summary}:")
		self.results_list.SetName(f"Results, {summary}")
		if not self.sounds:
			message = f"No library sounds found for {about}."
			self.set_status(message, spoken=False)
			self.results_label.SetLabel("Res&ults: none")
			self.update_buttons()
			self.search_box.SetFocus()
			wx.CallLater(300, speak, message)
			return
		self.set_status(f"Showing {summary} for {about}.", spoken=False)
		self.results_list.SetSelection(min(first_new, len(self.sounds) - 1))
		self.show_details()
		self.update_buttons()
		self.results_list.SetFocus()

	def on_similar(self, event):
		"""Find library sounds like the selected one."""
		sound = self.selected_sound()
		if not sound or self.busy:
			return
		if "path" not in sound:
			self.set_status("Find similar works on library sounds. Press Ctrl+2 to search your library.")
			return
		self.library_request = ("similar", sound["path"])
		self.library_limit = LIBRARY_PAGE_SIZE
		self.sounds = []
		self.results_list.Clear()
		self.details_box.Clear()
		self.fetch_library()

	def on_edit_notes(self, event):
		"""Add or change the notes on the selected library sound, so searches can find it by your words."""
		sound = self.selected_sound()
		if not sound or self.busy:
			return
		if "path" not in sound:
			self.set_status("Notes are added to Freesound sounds when you download them, with Ctrl+D.")
			return
		dialog = wx.TextEntryDialog(
			self,
			f"Notes for {sound.get('name')}. Describe it in your own words; searches will find these words.",
			"Edit notes",
			value=sound.get("notes") or "",
		)
		if dialog.ShowModal() == wx.ID_OK:
			notes = " ".join(dialog.GetValue().split())
			self.sound_index.set_notes(sound["path"], notes)
			sound["notes"] = notes
			index = self.results_list.GetSelection()
			self.results_list.SetString(index, self.list_line(sound))
			self.show_details()
			self.set_status("Notes saved." if notes else "Notes removed.")
		dialog.Destroy()
		self.results_list.SetFocus()

	def fetch_page(self):
		"""Fetch the current page of results in the background."""
		self.busy = True
		self.update_buttons()
		self.set_status(f"Searching for {self.query}...")
		sort = SORT_CHOICES[self.sort_choice.GetSelection()][1]
		max_duration = DURATION_CHOICES[self.length_choice.GetSelection()][1]
		query, page = self.query, self.page
		self.run_in_background(
			lambda: self.client.search(query, page, sort, max_duration),
			self.show_results,
			self.search_failed,
		)

	def show_results(self, reply):
		"""Add a page of results to the list and move focus to the first new one."""
		self.busy = False
		self.total = reply.get("count", 0)
		self.more_available = bool(reply.get("next"))
		new_sounds = reply.get("results") or []
		first_new = len(self.sounds)
		self.sounds.extend(new_sounds)
		for sound in new_sounds:
			self.results_list.Append(self.list_line(sound))
		summary = f"{len(self.sounds)} of {self.total} results"
		self.results_label.SetLabel(f"Res&ults, {summary}:")
		self.results_list.SetName(f"Results, {summary}")
		if not self.sounds:
			message = f"No sounds found for {self.query}."
			self.set_status(message, spoken=False)
			self.results_label.SetLabel("Res&ults: none")
			self.update_buttons()
			self.search_box.SetFocus()
			wx.CallLater(300, speak, message)
			return
		self.set_status(f"Showing {summary} for {self.query}.", spoken=False)
		self.results_list.SetSelection(first_new)
		self.show_details()
		self.update_buttons()
		self.results_list.SetFocus()

	def search_failed(self, message):
		"""Report a failed search."""
		self.busy = False
		if self.library_request and self.library_limit > LIBRARY_PAGE_SIZE:
			self.library_limit -= LIBRARY_PAGE_SIZE
		elif self.page > 1:
			self.page -= 1
		self.update_buttons()
		self.set_status(message, spoken=False)
		wx.MessageBox(message, APP_NAME, wx.OK | wx.ICON_WARNING, self)

	def on_select(self, event):
		"""Show the details of the newly selected sound."""
		self.show_details()
		self.update_buttons()

	def show_details(self):
		"""Fill the details box for the selected sound."""
		sound = self.selected_sound()
		if not sound:
			text = ""
		elif "path" in sound:
			text = make_library_details(sound)
		else:
			text = make_details(sound)
		self.details_box.SetValue(text)
		self.details_box.SetInsertionPoint(0)

	def on_list_key(self, event):
		"""Space in the results list plays or stops the preview."""
		if event.GetKeyCode() == wx.WXK_SPACE and not event.HasAnyModifiers():
			self.on_play(event)
		else:
			event.Skip()

	def on_char_hook(self, event):
		"""Escape stops the preview from anywhere in the window."""
		if event.GetKeyCode() == wx.WXK_ESCAPE and pygame.mixer.get_busy():
			self.stop_preview()
			self.set_status("Stopped.", spoken=False)
		else:
			event.Skip()

	def stop_preview(self):
		"""Stop whatever preview is playing."""
		pygame.mixer.stop()
		self.playing_id = None
		self.play_button.SetLabel("&Play preview")

	def on_play(self, event):
		"""Play the selected sound's preview, or stop it if it's already playing."""
		sound = self.selected_sound()
		if not sound:
			return
		if pygame.mixer.get_busy() and self.playing_id == self.sound_key(sound):
			self.stop_preview()
			self.set_status("Stopped.", spoken=False)
			return
		self.stop_preview()
		if "path" in sound:
			self.set_status(f"Loading {sound.get('name')}...", spoken=False)
			self.run_in_background(
				lambda: load_sound_file(sound["full_path"]),
				lambda loaded: self.start_playing(sound, loaded) if self.selected_sound() is sound else None,
				self.set_status,
			)
			return
		cached = self.preview_cache.get(sound["id"])
		if cached:
			self.play_bytes(sound, cached)
			return
		self.set_status(f"Loading the preview of {sound.get('name')}...", spoken=False)
		self.run_in_background(
			lambda: self.client.fetch_preview(sound),
			lambda data: self.preview_loaded(sound, data),
			self.set_status,
		)

	def preview_loaded(self, sound, data):
		"""Cache a fetched preview and play it if that sound is still selected."""
		self.preview_cache[sound["id"]] = data
		if self.selected_sound() is sound:
			self.play_bytes(sound, data)

	def play_bytes(self, sound, data):
		"""Play preview audio held in memory."""
		try:
			loaded = pygame.mixer.Sound(file=io.BytesIO(data))
		except pygame.error as error:
			self.set_status(f"Couldn't play the preview: {error}")
			return
		self.start_playing(sound, loaded)

	def start_playing(self, sound, loaded):
		"""Play a loaded sound and keep track of it so Space or Escape can stop it."""
		loaded.play()
		self.playing_id = self.sound_key(sound)
		self.play_button.SetLabel("Sto&p preview")
		self.set_status(f"Playing {sound.get('name')}. Space or Escape stops it.", spoken=False)
		self.watch_playback()

	def watch_playback(self):
		"""Put the Play button back when the preview finishes."""
		if pygame.mixer.get_busy():
			wx.CallLater(250, self.watch_playback)
		elif self.playing_id is not None:
			self.playing_id = None
			self.play_button.SetLabel("&Play preview")

	def ensure_signed_in(self):
		"""Make sure there's a working download token, asking the user to authorize if needed."""
		if self.client.has_working_token():
			return True
		if not self.client.client_id or not self.client.api_key:
			wx.MessageBox(
				"Downloading needs your own Freesound API credentials, the client ID and the client secret. "
				"Add them in Settings (F2).",
				APP_NAME, wx.OK | wx.ICON_WARNING, self,
			)
			return False
		dialog = SignInDialog(self, self.client)
		result = dialog.ShowModal()
		code = dialog.code
		dialog.Destroy()
		if result != wx.ID_OK or not code:
			self.set_status("Not signed in.")
			return False
		try:
			self.client.sign_in_with_code(code)
		except FreesoundError as error:
			wx.MessageBox(f"Signing in didn't work. {error}", APP_NAME, wx.OK | wx.ICON_WARNING, self)
			return False
		self.set_status("Signed in to Freesound.")
		return True

	def on_sign_in(self, event):
		"""Sign in to Freesound ahead of downloading."""
		if self.client.has_working_token():
			wx.MessageBox("You're already signed in.", APP_NAME, wx.OK, self)
			return
		self.ensure_signed_in()

	def on_download(self, event):
		"""Download the selected sound's original file and write its credits."""
		sound = self.selected_sound()
		if not sound or self.busy:
			return
		if "path" in sound:
			self.set_status("This sound is already in your library. Ctrl+O opens its folder.")
			return
		dialog = wx.TextEntryDialog(
			self,
			f"Notes about {strip_extension(sound.get('name'))}, if you'd like to add some. "
			"They're saved in its credit file, and your library search finds these words. Leave empty to skip.",
			"Notes for this sound",
		)
		answer = dialog.ShowModal()
		notes = dialog.GetValue()
		dialog.Destroy()
		if answer != wx.ID_OK:
			self.set_status("Download cancelled.")
			return
		if not self.ensure_signed_in():
			return
		self.downloads_folder().mkdir(parents=True, exist_ok=True)
		destination = self.downloads_folder() / safe_file_name(sound)
		self.busy = True
		self.update_buttons()
		self.set_status(f"Downloading {sound.get('name')}...")
		progress = {"last": 0}

		def report_progress(done, total):
			"""Show the download's progress in the status bar every 10 percent."""
			if total:
				percent = done * 100 // total
				if percent >= progress["last"] + 10:
					progress["last"] = percent
					wx.CallAfter(self.set_status, f"Downloading {sound.get('name')}, {percent} percent...", False)

		def work():
			"""Download the file, then write its credit files."""
			self.client.download_original(sound, destination, report_progress)
			write_credits(sound, destination, notes)

		self.run_in_background(
			work,
			lambda result: self.download_finished(sound, destination),
			self.download_failed,
		)

	def download_finished(self, sound, destination):
		"""Mark a sound as downloaded and say so."""
		self.busy = False
		self.downloaded_ids.add(sound["id"])
		index = self.sounds.index(sound)
		self.results_list.SetString(index, self.list_line(sound))
		self.update_buttons()
		self.set_status(f"Downloaded {strip_extension(sound.get('name'))}, with its credit.")

	def download_failed(self, message):
		"""Report a failed download."""
		self.busy = False
		self.update_buttons()
		self.set_status(message, spoken=False)
		wx.MessageBox(message, APP_NAME, wx.OK | wx.ICON_WARNING, self)

	def on_open_page(self, event):
		"""Open the selected sound's Freesound page in the browser, or a library sound's folder."""
		sound = self.selected_sound()
		if sound and "path" in sound:
			show_in_folder(sound["full_path"])
		elif sound:
			webbrowser.open(sound_page(sound))

	def on_copy_credit(self, event):
		"""Copy the selected sound's credit line to the clipboard."""
		sound = self.selected_sound()
		if not sound:
			return
		credit = (sound.get("credit") or "") if "path" in sound else make_credit_line(sound)
		if not credit:
			self.set_status("This sound has no credit recorded.")
			return
		if wx.TheClipboard.Open():
			wx.TheClipboard.SetData(wx.TextDataObject(credit))
			wx.TheClipboard.Close()
			self.set_status("Credit line copied.")

	def downloaded_file(self, sound):
		"""The downloaded file for a Freesound result, if it's in the downloads folder; otherwise None."""
		if "path" in sound:
			return None
		path = self.downloads_folder() / safe_file_name(sound)
		return path if path.exists() else None

	def on_add_to_library(self, event):
		"""Move the selected Freesound sound's download, with its credit file, into a library folder and index it."""
		sound = self.selected_sound()
		if not sound:
			return
		if "path" in sound:
			self.set_status("This sound is already in your library.")
			return
		path = self.downloaded_file(sound)
		if path is None:
			self.set_status("This sound isn't in your downloads folder. Download it first with Ctrl+D.")
			return
		self.move_into_library([path])

	def on_move_downloads(self, event):
		"""Move every downloaded sound, with its credit file, into a library folder and index them."""
		folder = self.downloads_folder()
		import sound_index
		paths = sorted(path for path in folder.iterdir() if path.is_file() and sound_index.is_sound_file(path)) if folder.is_dir() else []
		if not paths:
			self.set_status("There are no sounds in your downloads folder.")
			return
		self.move_into_library(paths)

	def move_into_library(self, paths):
		"""Ask for a library folder, move the sounds and their credit files there, and index that folder."""
		if self.indexing:
			self.set_status("Wait for the index update to finish first.")
			return
		if not self.settings["libraries"]:
			wx.MessageBox("Add a library folder in Settings (F2) first.", APP_NAME, wx.OK, self)
			return
		dialog = wx.DirDialog(self, f"Choose the library folder to move {len(paths)} sounds into", self.settings["libraries"][0]["folder"])
		answer = dialog.ShowModal()
		target = Path(dialog.GetPath())
		dialog.Destroy()
		if answer != wx.ID_OK:
			return
		if app_settings.key_for(self.settings, target) is None:
			wx.MessageBox("That folder isn't inside any of your library folders.", APP_NAME, wx.OK, self)
			return
		moved, skipped = [], []
		for path in paths:
			destination = target / path.name
			if destination.exists():
				skipped.append(path.name)
				continue
			shutil.move(str(path), str(destination))
			credit = path.with_suffix(".txt")
			if credit.exists() and not destination.with_suffix(".txt").exists():
				shutil.move(str(credit), str(destination.with_suffix(".txt")))
			moved.append(destination)
		message = f"Moved {count(len(moved), 'sound')} into {target.name}."
		if skipped:
			message += f" Skipped {count(len(skipped), 'sound')}, because a file of the same name is already there."
		self.set_status(message + " Indexing them..." if moved else message)
		if moved:
			self.start_indexing(only=target if len(moved) > 1 else moved[0])

	def on_add_credit(self, event):
		"""Add the selected sound's credit line to the credits list."""
		sound = self.selected_sound()
		if not sound:
			return
		credit = (sound.get("credit") or "") if "path" in sound else make_credit_line(sound)
		if not credit:
			self.set_status("This sound has no credit recorded, so there's nothing to add.")
			return
		if credit in self.credits_list:
			self.set_status(f"Already in the credits list, which has {count(len(self.credits_list), 'credit')}.")
			return
		self.credits_list.append(credit)
		save_credits_list(self.credits_list)
		self.set_status(f"Added to the credits list. It has {count(len(self.credits_list), 'credit')}.")

	def credits_text(self):
		"""The credits list as text, ready for a game's credits."""
		return "Sound credits\n\n" + "\n\n".join(self.credits_list) + "\n"

	def on_show_credits(self, event):
		"""Show the credits list in a box the screen reader can read through."""
		if not self.credits_list:
			self.set_status("The credits list is empty. Press Ctrl+K on a sound to add its credit.")
			return
		dialog = TextViewDialog(self, "Credits list", f"Credits list, {count(len(self.credits_list), 'credit')}:", self.credits_text())
		dialog.ShowModal()
		dialog.Destroy()

	def on_save_credits(self, event):
		"""Save the credits list to a text file."""
		if not self.credits_list:
			self.set_status("The credits list is empty. Press Ctrl+K on a sound to add its credit.")
			return
		dialog = wx.FileDialog(
			self, "Save the credits list", str(Path.home() / "Documents"), "sound credits.txt",
			wildcard="Text files (*.txt)|*.txt", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
		)
		if dialog.ShowModal() == wx.ID_OK:
			Path(dialog.GetPath()).write_text(self.credits_text(), encoding="utf-8")
			self.set_status(f"Saved {count(len(self.credits_list), 'credit')} to {Path(dialog.GetPath()).name}.")
		dialog.Destroy()

	def on_clear_credits(self, event):
		"""Empty the credits list, after asking."""
		if not self.credits_list:
			self.set_status("The credits list is already empty.")
			return
		if wx.MessageBox(f"Clear all {len(self.credits_list)} credits from the list?", APP_NAME, wx.YES_NO, self) == wx.YES:
			self.credits_list.clear()
			save_credits_list(self.credits_list)
			self.set_status("Credits list cleared.")

	def run_first_setup(self):
		"""Walk a new user through credentials, library folders and downloads, then offer to index."""
		if self.indexing or self.lookup_running or self.busy:
			self.set_status("Wait for the current job to finish first.")
			return
		answer = wx.MessageBox(
			f"Welcome to {APP_NAME}.\n\nThis short setup asks for your Freesound credentials, the folders of sounds you "
			"want to search, and where downloads go. Everything can be changed later in Settings (F2).\n\n"
			"Press OK to start, or Cancel to skip.",
			"Setup", wx.OK | wx.CANCEL, self,
		)
		if answer == wx.OK:
			dialog = CredentialsDialog(self, self.settings)
			if dialog.ShowModal() == wx.ID_OK:
				new_id = dialog.id_box.GetValue().strip()
				if new_id != self.settings["freesound_client_id"]:
					self.client.forget_sign_in()
				self.settings["freesound_client_id"] = new_id
				self.settings["freesound_client_secret"] = dialog.secret_box.GetValue().strip()
			dialog.Destroy()
			question = "Add a folder of your own sounds, so you can search it with My library?"
			while wx.MessageBox(question, "Setup", wx.YES_NO, self) == wx.YES:
				library = choose_library_folder(self, self.settings)
				if library:
					self.settings["libraries"].append(library)
				question = "Add another folder of sounds?"
			keep = wx.MessageBox(
				f"Downloads go in {self.settings['downloads_folder']}. Keep that? Choose No to pick another folder.",
				"Setup", wx.YES_NO, self,
			)
			if keep == wx.NO:
				folder_dialog = wx.DirDialog(self, "Choose the folder for downloads", self.settings["downloads_folder"])
				if folder_dialog.ShowModal() == wx.ID_OK:
					self.settings["downloads_folder"] = folder_dialog.GetPath()
				folder_dialog.Destroy()
		app_settings.save(self.settings)
		self.sound_index = None
		self.fill_library_choice()
		self.downloaded_ids = self.read_downloaded_ids()
		import sound_index
		if answer == wx.OK and self.settings["libraries"] and not sound_index.model_ready():
			self.start_indexing()
		elif answer == wx.OK and self.settings["libraries"] and wx.MessageBox(
			"Index your library now? A big library takes a while. You can search Freesound meanwhile, and press Ctrl+U to stop.",
			"Setup", wx.YES_NO, self,
		) == wx.YES:
			self.start_indexing()
		else:
			self.set_status("Setup finished. Press F1 for the list of keys.")
		self.search_box.SetFocus()

	def on_open_downloads(self, event):
		"""Open the downloads folder in the file manager."""
		self.downloads_folder().mkdir(parents=True, exist_ok=True)
		wx.LaunchDefaultApplication(str(self.downloads_folder()))

	def on_about(self, event):
		"""Show the app's name and version."""
		wx.MessageBox(
			f"{APP_NAME} {APP_VERSION}\nCerulean Blue Software\nBe True, Be You, Be Blue!\n\n"
			"Sounds and data from Freesound.org.",
			"About", wx.OK, self,
		)

	def on_close(self, event):
		"""Stop sound and close the window."""
		pygame.mixer.quit()
		event.Skip()


def main():
	"""Start the app."""
	app = wx.App()
	first_run = not app_settings.SETTINGS_FILE.exists()
	settings = app_settings.load()
	pygame.mixer.init(44100)
	window = MainWindow(settings, FreesoundClient(settings))
	window.Show()
	if first_run:
		wx.CallAfter(window.run_first_setup)
	app.MainLoop()


if __name__ == "__main__":
	main()
