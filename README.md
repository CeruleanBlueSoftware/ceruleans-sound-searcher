# Cerulean's Sound Searcher

A sound search and credit tool for game developers, built fully accessible from the start. It works with a screen reader and the keyboard throughout, and it's made by Cerulean Blue Software, a blind game developer.

The aim is simple: make it easy to find the right sound, know its licence, and give its creator credit.

Be True, Be You, Be Blue!

[Cerulean Blue Software](https://ceruleanbluesoftware.com) | Read me | [Change log](CHANGELOG.md)

## What it does

- **Searches Freesound** with your own free Freesound API credentials. Results show each sound's length, licence and creator, play as previews, and download as the original file.
- **Writes a credit file for every download**, with the credit line, licence, creator and link, and keeps a running CREDITS.txt in your downloads folder. CC0 sounds get credited too.
- **Searches your own sound library by what the sounds actually sound like.** Type "heavy wooden door creaking" and the closest matches come first, even when the file names say nothing useful. This uses the CLAP audio model, which runs on your own computer.
- **Finds similar sounds** to the one you've picked.
- **Takes your notes.** Describe a sound in your own words, such as "healing spell shimmer", and searches will find it by those words. You can add notes when downloading, or to any library sound.
- **Keeps licences in view.** Every library sound shows its licence, read from credit files, Freesound pack readmes, or rules you set for whole folders. "Cleared licences only" hides anything that isn't allowed in a game you sell, or whose licence is unknown.
- **Looks up missing licences.** For library sounds named with a Freesound number but missing their licence, it asks Freesound and writes the credit file for you.

## Requirements

- Windows 10 or later.
- Python 3.12.
- About 3 GB of disk space for the Python packages and the sound model.
- A screen reader is optional; speech goes through your screen reader when one is running, using accessible_output2.

## Setting up

1. Download or clone this repository into a folder.
2. Open a command prompt in that folder and create a virtual environment called .win:

	```
	py -3.12 -m venv .win
	```

3. Install the packages:

	```
	.win\Scripts\pip install -r requirements.txt
	```

4. Open "run sound searcher.bat" to start the app.

The first library search or index downloads the CLAP model (about 600 MB) from Hugging Face. After that it works offline.

## Your Freesound credentials

Searching Freesound needs your own API credentials. They're free.

1. Sign in to Freesound, then open https://freesound.org/apiv2/apply (Settings has a button that opens it).
2. Apply for credentials. For the callback address, enter http://localhost:8765/callback so signing in finishes by itself.
3. In the app, press F2 for Settings and enter your client ID and client secret.

The first time you download, the app opens Freesound's Authorize page in your browser. Sign in there if asked and press Authorize. The app never sees your Freesound password.

Searching your own library works without any credentials.

## Your library

1. Press F2 for Settings, and under Folders choose "Add library folder". Give each folder a short name.
2. Open "index sounds.bat". It listens to every sound in your library folders and builds the index. A big library takes a while the first time; after that, only new or changed sounds are indexed.
3. In the app, press Ctrl+2 and search.

"index report.txt", beside the index, lists how many sounds are cleared for use and which folders have no licence information. For those folders, add a licence rule in Settings: where the sounds came from, their licence, whether they're allowed in games you sell, and the credit line.

## Keys

- F1: list of keys.
- F2: Settings.
- Ctrl+1: search Freesound. Ctrl+2: search your library.
- Enter in the search box: search.
- Space in the results list: play or stop the sound. Escape also stops it.
- Ctrl+D: download the selected Freesound sound, with its credit and any notes.
- Ctrl+M: find library sounds like the selected one.
- Ctrl+E: edit the selected library sound's notes.
- Ctrl+N: more results.
- Ctrl+O: open the sound's Freesound page, or a library sound's folder.
- Ctrl+Shift+C: copy the credit line.
- Ctrl+L: open the downloads folder.
- Ctrl+F: back to the search box.

## Where your things are kept

Nothing personal is kept in the program folder. Your settings, Freesound credentials and sign-in, the library index and your notes are in AppData\Roaming\Cerulean Blue Software\Cerulean's Sound Searcher. Keep settings.json and oauth_token.json private: they hold your credentials and sign-in.

## Status

Version 0.2.0, an early release. It runs from Python for now. A smaller, installable version is planned. See the [change log](CHANGELOG.md) for what's new.

## Feedback and contributions

Issues and suggestions are welcome: open an issue on this repository. Cerulean Blue Software is a solo developer, so replies may take a little while.

Accessibility reports are especially valued. If anything is hard to reach with a screen reader or the keyboard, or isn't announced clearly, please say so, and mention your screen reader.

Pull requests are welcome too. Please keep everything usable by keyboard and screen reader, and describe how you tested that.

## Licence and credits

The code is released under the MIT licence; see LICENSE. The Cerulean Blue Software name and logo are not covered by that licence.

- Sounds and data come from [Freesound](https://freesound.org), through its API. Cerulean's Sound Searcher is not made by or affiliated with Freesound.
- Library search uses LAION's CLAP model, [laion/larger_clap_general](https://huggingface.co/laion/larger_clap_general), under the Apache 2.0 licence.
- The sound labels (audioset_labels.txt) are the category names of Google's [AudioSet ontology](https://research.google.com/audioset/ontology/index.html), under the Creative Commons Attribution 4.0 licence.
