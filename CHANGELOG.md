# Cerulean's Sound Searcher Change Log

Be True, Be You, Be Blue!

[Cerulean Blue Software](https://ceruleanbluesoftware.com) | [Read me](README.md) | Change log

## Version 0.4.0, 22 November 2026

### Changed

- The sound model now runs with ONNX Runtime instead of PyTorch, so the app needs about 200 MB of packages instead of about 3 GB. Search results are exactly the same, and existing indexes keep working.
- The sound model is downloaded once, with spoken progress, the first time you search or index your library, instead of being fetched from Hugging Face. You can stop the download and carry on later.

## Version 0.3.0, 22 November 2026

The first public release: easier to start with, and tools for filing sounds and gathering credits.

### New

- **First-time setup:** the first time the app opens, it asks for your Freesound credentials, your folders of sounds and where downloads go, then offers to index your library. Every step can be skipped, and the setup can be run again from the File menu.
- **Update library index** (Ctrl+U) from inside the app, with progress spoken about once a minute. You can keep searching while it runs, and Ctrl+U again stops it. "index sounds.bat" still works too.
- When the app starts, it tells you if sounds have been added to, changed in or removed from your library since it was indexed.
- **Library box:** search all your libraries, or just one.
- **Add download to library** (Ctrl+Shift+A) and **Move all downloads to library** (File menu): move downloads, with their credit files, into a library folder, and index them straight away.
- **Credits list:** Ctrl+K adds the selected sound's credit. The File menu shows the list, saves it as a text file for your game's credits, or clears it. The list is kept between sessions.
- **Index file in Settings:** choose a different index, start a new one, or back up the one in use.
- **Open index report** in the File menu.

### Changed

- Downloads go in a downloads folder inside the settings folder, unless you choose another.

## Version 0.2.0, 22 November 2026

Library search, licences and settings.

### New

- **Search your own sound library** (Ctrl+2) by what the sounds actually sound like, using the CLAP audio model on your own computer. Each result gives its name, length, what it sounds like, and its licence.
- **Find similar** (Ctrl+M) lists the library sounds closest to the selected one.
- **Notes:** describe a sound in your own words, and searches find it by them. Add notes to any library sound with Ctrl+E, or when downloading from Freesound; download notes are saved in the sound's credit file.
- **Licences in view:** library sounds show their licence, read from credit files, Freesound pack readmes, or licence rules you set for whole folders. "Cleared licences only" hides sounds not allowed in games you sell, or whose licence is unknown.
- **Look up licences on Freesound** (File menu): for library sounds named with a Freesound number but missing their licence, it asks Freesound and writes the credit file.
- **Settings** (F2): your own Freesound credentials, the licences, channels and sample rate to search for, the downloads folder, library folders, and licence rules.
- "index sounds.bat" builds and updates the library index, and writes a report of licences and any unreadable files.

### Changed

- Settings, the Freesound sign-in, the index and your notes now live in your own AppData folder, never beside the program.
- Freesound searches follow the licence, channel and sample rate choices in Settings, instead of always stereo, 44,100 Hz, CC0 or CC BY.
- Space plays library sounds from their files; Ctrl+O opens a library sound's folder.

## Version 0.1.0, 10 November 2026

The first version, for Cerulean Blue Software's own use.

- Search Freesound for sounds, with length and sort filters.
- Play previews with Space.
- Download original files after signing in to Freesound once, with a credit file beside each download and a running CREDITS.txt.
- Copy a sound's credit line, and open its Freesound page.
- Speech through your screen reader for searches, downloads and sign-in.
