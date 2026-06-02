# GoToSpoty

GoToSpoty is a Python CLI tool that scrapes a Spotify playlist or album in a
Chrome browser session, searches each track with `yt-dlp`, downloads the best
matching audio, and stores the result as tagged audio files.

By default it writes Opus files under `output/<album>/<title>.opus`, embeds
Spotify artwork when available, and saves artist, album, and title metadata.

## Features

- Scrapes Spotify playlists and albums through Selenium/Chrome.
- Persists the Spotify login session in a local Chrome profile directory.
- Downloads audio with `yt-dlp`.
- Supports `opus`, `mp3`, `aac`, `flac`, and `m4a` output codecs.
- Adds metadata and cover art with Mutagen.
- Skips tracks that already exist in the output directory.
- Optional dry-run mode for validating scraping before downloading.
- Optional export to a Kopuz playlist file.

## Requirements

- Python 3.12.7 or newer
- `uv`
- Google Chrome or Chromium
- A Selenium-compatible ChromeDriver setup
- `ffmpeg` available on `PATH`
- A Spotify account for browser login

`yt-dlp` uses `ffmpeg` for audio extraction and conversion, so downloads will
fail if `ffmpeg` is missing.

## Installation

Install dependencies from the lockfile:

```bash
uv sync
```

## Usage

Run the tool with a Spotify playlist or album URL:

```bash
uv run python main.py "https://open.spotify.com/playlist/..."
```

Or omit the URL and paste it when prompted:

```bash
uv run python main.py
```

The first time you run it, Chrome opens at the Spotify login page. Log in, then
return to the terminal and press Enter. The session is stored in
`selenium_session` by default, so later runs can reuse the same login.

Downloaded files are written to `output` unless you choose another directory:

```bash
uv run python main.py "https://open.spotify.com/album/..." --output ~/Music/GoToSpoty
```

## Examples

Download a playlist as Opus files:

```bash
uv run python main.py "https://open.spotify.com/playlist/..."
```

Download as MP3:

```bash
uv run python main.py "https://open.spotify.com/playlist/..." --codec mp3
```

Scrape tracks without downloading audio:

```bash
uv run python main.py "https://open.spotify.com/playlist/..." --dry-run
```

Use a custom Selenium session directory:

```bash
uv run python main.py "https://open.spotify.com/playlist/..." --session-dir .spotify-session
```

Write logs to a file:

```bash
uv run python main.py "https://open.spotify.com/playlist/..." --log-file gotospoty.log
```

Export downloaded tracks into Kopuz after downloading:

```bash
uv run python main.py "https://open.spotify.com/playlist/..." --export-kopuz-playlist "Road Trip"
```

## Options

```text
PLAYLIST_URL                  Spotify playlist or album URL. Prompted if omitted.
-o, --output DIR              Root directory for downloaded files.
--codec CODEC                 Output codec: opus, mp3, aac, flac, or m4a.
--format YDL_FORMAT           yt-dlp format selector.
--session-dir DIR             Chrome user-data directory for Spotify login state.
--scroll-sleep SEC            Delay between Spotify scroll steps.
--stuck-threshold N           Stop scrolling after N unchanged iterations.
--headless                    Pass the headless mode flag to Chrome.
--dry-run                     Scrape metadata without downloading audio.
--remote-components VALUE     Accepted by the CLI, but not used by the script.
--export-kopuz-playlist NAME  Append downloaded tracks to a Kopuz playlist.
--log-level LEVEL             DEBUG, INFO, WARNING, or ERROR.
--log-file PATH               Also write logs to a plain-text file.
--no-color                    Disable ANSI color output.
```

## Output Layout

The default output layout is:

```text
output/
  Album Name/
    Track Title.opus
```

For playlists, the script uses the album name from each Spotify track row when
available. For albums, it uses the page album title.

## Kopuz Export

When `--export-kopuz-playlist NAME` is provided, GoToSpoty appends a playlist to:

```text
~/.config/kopuz/playlists.json
```

If the file already exists, the tool asks whether to create a
`playlists.json.bak` backup before writing.

## Project Structure

```text
.
├── main.py          # CLI, scraper, downloader, metadata, and Kopuz export
├── pyproject.toml   # Project metadata and dependencies
├── uv.lock          # Locked dependency versions
└── README.md        # Project documentation
```

## Development

Install dependencies:

```bash
uv sync
```

Show the CLI help:

```bash
uv run python main.py --help
```

Run a scrape-only test before downloading:

```bash
uv run python main.py "https://open.spotify.com/playlist/..." --dry-run --log-level DEBUG
```

There is no automated test suite in the repository yet. For changes to scraping
or downloading behavior, validate with `--dry-run` first, then with a small
playlist.

## Troubleshooting

If Chrome does not start, check that Chrome or Chromium is installed and that
Selenium can find a compatible driver.

If audio conversion fails, make sure `ffmpeg` is installed and available on
`PATH`:

```bash
ffmpeg -version
```

If Spotify pages load but no tracks are found, try increasing the scroll delay:

```bash
uv run python main.py "https://open.spotify.com/playlist/..." --scroll-sleep 2
```

If login state is broken or stale, use a different session directory:

```bash
uv run python main.py "https://open.spotify.com/playlist/..." --session-dir .spotify-session-new
```

## Contributing

Contributions are welcome. Useful areas include:

- Better track matching and disambiguation for `yt-dlp` search results.
- Safer filename handling across platforms.
- Automated tests for parsing and metadata behavior.
- More robust Spotify page scraping when the web UI changes.
- Clearer packaging or entry-point support.

Before opening a pull request:

1. Run `uv sync`.
2. Check `uv run python main.py --help`.
3. Test relevant changes with `--dry-run`.
4. Keep changes focused and update this README when behavior changes.

## License

The project is licensed under the GPL License. See `LICENSE` for details.

## Notes

- The tool downloads audio by searching for track names and artists with
  `yt-dlp`; it does not download audio from Spotify.
- Search results may not always match the exact Spotify track.
- `--headless` is mainly useful after an existing session is available; the
  script still asks for login confirmation before scraping.
- File names are built from Spotify track titles, so unusual characters in
  titles may affect filesystem behavior on some platforms.
- Use this tool only for content you are allowed to download.
