# Cerulean's Sound Searcher Change Log

Be True, Be You, Be Blue!

[Cerulean Blue Software](https://ceruleanbluesoftware.com) | [Read me](README.md) | Change log

## Version 0.2.0, 22 November 2026

The first public release.

### New

- **Search your own sound library** (Ctrl+2) by what the sounds actually sound like, using the CLAP audio model on your own computer. Each result gives its name, length, what it sounds like, and its licence.
- **Find similar** (Ctrl+M) lists the library sounds closest to the selected one.
- **Notes:** describe a sound in your own words, and searches find it by them. Add notes to any library sound with Ctrl+E, or when downloading from Freesound; download notes are saved in the sound's credit file.
- **Licences in view:** library sounds show their licence, read from credit files, Freesound pack readmes, or licence rules you set for whole folders. "Cleared licences only" hides sounds not allowed in games you sell, or whose licence is unknown.
- **Look up licences on Freesound** (File menu): for library sounds named with a Freesound number but missing their licence, it asks Freesound and writes the credit file.
- **Settings** (F2): your own Freesound credentials, the licences, channels and sample rate to search for, the downloads folder, library folders, and licence rules.
- "index sounds.bat" builds and updates the library index, and writes a report of licences and any unreadable files.

### Changed

- Settings, the Freesound sign-in, the index and your notes now live in your own AppData folder, never beside the program. Downloads go in a downloads folder there too, unless you choose another.
- Freesound searches follow the licence, channel and sample rate choices in Settings, instead of always stereo, 44,100 Hz, CC0 or CC BY.
- Space plays library sounds from their files; Ctrl+O opens a library sound's folder.

## Version 0.1.0, 10 November 2026

The first version, for Cerulean Blue Software's own use.

- Search Freesound for sounds, with length and sort filters.
- Play previews with Space.
- Download original files after signing in to Freesound once, with a credit file beside each download and a running CREDITS.txt.
- Copy a sound's credit line, and open its Freesound page.
- Speech through your screen reader for searches, downloads and sign-in.
