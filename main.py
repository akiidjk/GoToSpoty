import argparse
import base64
import json
import logging
import re
import shutil
import sys
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from bs4 import BeautifulSoup
from mutagen.flac import Picture
from mutagen.oggopus import OggOpus
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from yt_dlp import YoutubeDL

# ---------------------------------------------
#  ANSI color palette
# ---------------------------------------------


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BGREEN = "\033[92m"
    BRED = "\033[91m"
    BYELLOW = "\033[93m"
    BCYAN = "\033[96m"
    BWHITE = "\033[97m"

    @staticmethod
    def strip(text: str) -> str:
        """Remove ANSI codes (used when color is disabled)."""
        return re.sub(r"\033\[[0-9;]*m", "", text)


def _supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


# ---------------------------------------------
#  Colored logging formatter
# ---------------------------------------------

LEVEL_STYLES = {
    logging.DEBUG: (C.DIM + C.WHITE, "DEBUG  "),
    logging.INFO: (C.BCYAN, "INFO   "),
    logging.WARNING: (C.BYELLOW, "WARN   "),
    logging.ERROR: (C.BRED, "ERROR  "),
    logging.CRITICAL: (C.BOLD + C.BRED, "FATAL  "),
}


class ColorFormatter(logging.Formatter):
    def __init__(self, use_color: bool = True):
        super().__init__()
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        color, label = LEVEL_STYLES.get(record.levelno, (C.RESET, "???    "))
        ts = self.formatTime(record, "%H:%M:%S")

        if self.use_color:
            prefix = f"{C.DIM}{ts}{C.RESET}  {color}{C.BOLD}{label}{C.RESET} "
        else:
            prefix = f"{ts}  {label} "

        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)

        return prefix + msg


def build_logger(
    name: str, level: int, log_file: Optional[str], use_color: bool
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(ColorFormatter(use_color=use_color))
    logger.addHandler(ch)

    # Optional file handler (no color)
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(ColorFormatter(use_color=False))
        logger.addHandler(fh)

    return logger


# ---------------------------------------------
#  Config dataclass
# ---------------------------------------------


@dataclass
class Config:
    output_path: str = "output"
    login_url: str = "https://accounts.spotify.com/en/login"
    session_dir: str = "selenium_session"
    audio_codec: str = "opus"
    audio_format: str = "bestaudio"
    scroll_sleep: float = 1.0
    page_load_sleep: float = 2.0
    stuck_threshold: int = 5
    log_level: str = "INFO"
    log_file: Optional[str] = None
    no_color: bool = False
    headless: bool = False
    dry_run: bool = False
    playlist_url: Optional[str] = None
    export_kopuz_playlist: Optional[str] = None
    # Resolved at runtime
    _logger: logging.Logger = field(
        init=False,
        repr=False,
    )
    _logger_initialized: bool = False

    def setup_logger(self) -> logging.Logger:
        level = getattr(logging, self.log_level.upper(), logging.INFO)
        use_color = not self.no_color and _supports_color()
        self._logger = build_logger("scraper", level, self.log_file, use_color)
        return self._logger

    @property
    def logger(self) -> logging.Logger:
        if not self._logger_initialized:
            self.setup_logger()
            self._logger_initialized = True
        return self._logger


# ---------------------------------------------
#  Data model
# ---------------------------------------------


class Music:
    def __init__(
        self, title: str, artist: str, image_url: str | None, album: str
    ) -> None:
        self.title = title
        self.artist = artist
        self.image_url = image_url
        self.album = album

    def __eq__(self, other):
        if not isinstance(other, Music):
            return NotImplemented
        return self.title == other.title and self.artist == other.artist

    def __hash__(self):
        return hash((self.title, self.artist))

    def __repr__(self):
        return f"<Music '{self.title}' by '{self.artist}'>"


# ---------------------------------------------
#  Banner
# ---------------------------------------------

BANNER = r"""
  ____     _____    ____              _
 / ___| __|_   _|__/ ___| _ __   ___ | |_ _   _
| |  _ / _ \| |/ _ \___ \| '_ \ / _ \| __| | | |
| |_| | (_) | | (_) |__) | |_) | (_) | |_| |_| |
 \____|\___/|_|\___/____/| .__/ \___/ \__|\__, |
Spotify → Opus Dumper    |_|               |___/
"""


def print_banner(cfg: Config):
    if not cfg.no_color and _supports_color():
        print(f"{C.BOLD}{C.BGREEN}{BANNER}{C.RESET}")
    else:
        print(BANNER)


# ---------------------------------------------
#  Scraper
# ---------------------------------------------


class Scraper:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.log = cfg.logger
        self.musics: List[Music] = []

        chrome_opts = Options()
        chrome_opts.add_argument(f"user-data-dir={cfg.session_dir}")
        if cfg.headless:
            chrome_opts.add_argument("--headless=new")
            self.log.debug("Chrome running in headless mode")
        self.driver = webdriver.Chrome(options=chrome_opts)

    # -- Auth ----------------------------------

    def login(self):
        self.log.info(f"Opening login page → {self.cfg.login_url}")
        self.driver.get(self.cfg.login_url)
        input(
            f"\n  {C.BOLD}{C.BYELLOW}➜  Log in to Spotify, then press ENTER …{C.RESET}\n"
        )
        self.log.info("Login confirmed by user")

    # -- Scraping ------------------------------

    def scrape(self):
        url = (
            self.cfg.playlist_url
            or input(
                f"\n  {C.BOLD}{C.BCYAN}➜  Paste the playlist / album URL: {C.RESET}"
            ).strip()
        )

        self.log.info(f"Navigating to: {url}")
        self.driver.get(url)
        time.sleep(self.cfg.page_load_sleep)

        page_album = ""
        if "album" in url:
            try:
                el = self.driver.find_element(
                    By.XPATH,
                    '//*[@id="main-view"]/div/div[2]/div[1]/div/main/section/div[1]/div[2]/div[2]/span[2]/span/h1',
                )
                page_album = el.text.strip()
                self.log.info(f"Album title detected: {page_album}")
            except Exception as e:
                self.log.debug(f"Could not read album title from h1: {e}")

        playlist_size = self._get_playlist_size()

        self.log.info("Scrolling and collecting track rows …")
        track_htmls: set[str] = set()
        stuck_count = 0

        while True:
            rows = self.driver.find_elements(
                By.CSS_SELECTOR, '[data-testid="tracklist-row"]'
            )
            if not rows:
                self.log.warning("No track rows found — stopping scroll")
                break

            before = len(track_htmls)
            for row in rows:
                html = row.get_attribute("outerHTML")
                if html is not None:
                    track_htmls.add(html)

            found = len(track_htmls)
            self.log.debug(f"Rows collected so far: {found}")

            if found == before:
                stuck_count += 1
                if stuck_count >= self.cfg.stuck_threshold:
                    self.log.debug(f"Stuck for {stuck_count} iterations — stopping")
                    break
            else:
                stuck_count = 0

            if playlist_size and found >= playlist_size:
                break

            self.driver.execute_script("arguments[0].scrollIntoView();", rows[-1])
            time.sleep(self.cfg.scroll_sleep)

        self._parse_tracks(track_htmls, page_album)
        self.log.info(f"{C.BGREEN}Scraped {len(self.musics)} unique tracks{C.RESET}")

    def _get_playlist_size(self) -> int:
        try:
            el = self.driver.find_element(By.XPATH, "//span[contains(text(), ' song')]")
            size = int(el.text.replace(",", "").split()[0])
            self.log.info(f"Playlist declares {size} songs")
            return size
        except Exception as e:
            self.log.debug(f"Could not read playlist size: {e}")
            return 0

    def _parse_tracks(self, htmls: set[str], page_album: str = ""):
        for html in htmls:
            soup = BeautifulSoup(html, "html.parser")

            title_elem = soup.select_one('a[href^="/track/"]')
            artist_elems = soup.select('a[href^="/artist/"]')
            album_elem = soup.select_one('a[href^="/album/"]')
            img_elem = soup.select_one("img")

            title = title_elem.text.strip() if title_elem else ""
            artists = ", ".join(a.text.strip() for a in artist_elems)

            album = ""
            if page_album:
                album = page_album
            elif album_elem:
                album = album_elem.text.strip()

            img_url = img_elem.get("src") if img_elem else ""

            if not title:
                continue

            self.musics.append(
                Music(title=title, artist=artists, album=album, image_url=str(img_url))
            )

        self.musics = list(set(self.musics))

    # -- Metadata ------------------------------

    def _add_metadata(self, music: Music):
        path = Path(self.cfg.output_path) / music.album / f"{music.title}.opus"
        if not path.exists():
            self.log.warning(f"File not found for tagging: {path}")
            return

        audio = OggOpus(str(path))

        if music.image_url:
            try:
                hi_res_url = music.image_url.replace("4851", "1e02")
                req = urllib.request.Request(
                    hi_res_url, headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req) as resp:
                    image_data = resp.read()

                pic = Picture()
                pic.data = image_data
                pic.type = 3
                pic.mime = "image/jpeg"
                audio["metadata_block_picture"] = base64.b64encode(pic.write()).decode(
                    "ascii"
                )
                self.log.debug(f"Embedded artwork for '{music.title}'")
            except Exception as e:
                self.log.warning(f"Could not embed artwork for '{music.title}': {e}")

        audio["artist"] = music.artist
        audio["album"] = music.album
        audio["title"] = music.title
        audio.save()
        self.log.debug(f"Tags saved → {path.name}")

    # -- Download ------------------------------

    def download(self):
        total = len(self.musics)
        ok = 0
        skipped = 0
        failed = 0

        ydl_opts = {
            "format": self.cfg.audio_format,
            "quiet": self.cfg.log_level != "DEBUG",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": self.cfg.audio_codec},
                {"key": "FFmpegMetadata"},
            ],
        }

        self.log.info(f"Starting download of {total} track(s) …")

        for idx, music in enumerate(self.musics, 1):
            out_dir = Path(self.cfg.output_path) / music.album
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / music.title

            if Path(f"{out_path}.{self.cfg.audio_codec}").exists():
                self.log.info(
                    f"[{idx:>{len(str(total))}}/{total}] "
                    f"{C.DIM}SKIP{C.RESET}  {music.title}"
                )
                skipped += 1
                continue

            query = f"{music.title} {music.artist}"
            self.log.info(
                f"[{idx:>{len(str(total))}}/{total}] "
                f"{C.BCYAN}↓{C.RESET}  {C.BOLD}{music.title}{C.RESET}"
                f"  {C.DIM}by {music.artist}{C.RESET}"
            )

            if self.cfg.dry_run:
                self.log.debug(f"  [dry-run] would search: ytsearch:{query}")
                ok += 1
                continue

            ydl_opts["outtmpl"] = str(out_path)
            try:
                with YoutubeDL(ydl_opts) as ydl:
                    ydl.download([f"ytsearch:{query}"])
                self._add_metadata(music)
                self.log.debug(f"  {C.BGREEN}✓{C.RESET} Done")
                ok += 1
            except Exception as e:
                self.log.error(f"  Failed to download '{query}': {e}")
                failed += 1

        # -- Summary ---------------------------
        self.log.info(
            f"\n  {C.BOLD}Download summary{C.RESET}\n"
            f"  {C.BGREEN}✓ OK      {ok}{C.RESET}\n"
            f"  {C.DIM}⊘ Skipped {skipped}{C.RESET}\n"
            f"  {C.BRED}✗ Failed  {failed}{C.RESET}"
        )

    # -- Kopuz Playlist Export -----------------

    def export_kopuz_playlist(self):
        if not self.cfg.export_kopuz_playlist:
            return

        playlist_name = self.cfg.export_kopuz_playlist
        kopuz_path = Path.home() / ".config" / "kopuz" / "playlists.json"

        self.log.info(f"Exporting to Kopuz playlist: {playlist_name}")

        # Ask for backup
        if kopuz_path.exists():
            response = (
                input(
                    f"\n  {C.BOLD}{C.BYELLOW}➜  Kopuz playlist file exists. Create backup? (y/N): {C.RESET}"
                )
                .strip()
                .lower()
            )
            if response in ["y", "yes"]:
                backup_path = kopuz_path.with_suffix(".json.bak")
                shutil.copy2(kopuz_path, backup_path)
                self.log.info(f"Backup created: {backup_path}")

        # Load existing data or create new
        if kopuz_path.exists():
            with open(kopuz_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"playlists": [], "jellyfin_playlists": [], "folders": []}
            kopuz_path.parent.mkdir(parents=True, exist_ok=True)

        # Collect track paths
        tracks = []
        for music in self.musics:
            track_path = (
                Path(self.cfg.output_path)
                / music.album
                / f"{music.title}.{self.cfg.audio_codec}"
            )
            if track_path.exists():
                tracks.append(str(track_path.resolve()))

        new_playlist = {
            "id": str(uuid.uuid4()),
            "name": playlist_name,
            "tracks": tracks,
            "cover_path": None,
        }

        # Append to playlists
        data["playlists"].append(new_playlist)

        # Save
        with open(kopuz_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        self.log.info(
            f"{C.BGREEN}Exported {len(tracks)} tracks to Kopuz playlist '{playlist_name}'{C.RESET}"
        )

    # -- Entry point ---------------------------

    def start(self):
        self.login()
        self.scrape()
        self.driver.quit()
        self.download()
        self.export_kopuz_playlist()
        input(f"\n  {C.DIM}Press ENTER to exit …{C.RESET}\n")


# ---------------------------------------------
#  CLI
# ---------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gotospoty",
        description="Scrape a Spotify playlist/album and download audio as Opus files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    p.add_argument(
        "url",
        nargs="?",
        metavar="PLAYLIST_URL",
        help="Spotify playlist or album URL (prompted interactively if omitted)",
    )
    p.add_argument(
        "-o",
        "--output",
        default="output",
        metavar="DIR",
        help="Root directory for downloaded files",
    )
    p.add_argument(
        "--codec",
        default="opus",
        choices=["opus", "mp3", "aac", "flac", "m4a"],
        help="Audio codec for the output files",
    )
    p.add_argument(
        "--format",
        default="bestaudio",
        metavar="YDL_FORMAT",
        dest="audio_format",
        help="yt-dlp format selector",
    )
    p.add_argument(
        "--session-dir",
        default="selenium_session",
        metavar="DIR",
        help="Chrome user-data directory for persisting the Spotify session",
    )
    p.add_argument(
        "--scroll-sleep",
        type=float,
        default=1.0,
        metavar="SEC",
        help="Seconds to wait between scroll steps",
    )
    p.add_argument(
        "--stuck-threshold",
        type=int,
        default=5,
        metavar="N",
        help="Stop scrolling after N iterations with no new tracks",
    )
    p.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome in headless mode (skips the interactive login)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape metadata but do not download any audio",
    )
    p.add_argument(
        "--remote-components",
        default="ejs:github",
        help="Remote components",
    )
    p.add_argument(
        "--export-kopuz-playlist",
        metavar="NAME",
        help="Export downloaded tracks to Kopuz playlist with the given name",
    )

    log_group = p.add_argument_group("logging")
    log_group.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        metavar="LEVEL",
        help="Console log verbosity (DEBUG | INFO | WARNING | ERROR)",
    )
    log_group.add_argument(
        "--log-file",
        default=None,
        metavar="PATH",
        help="Also write logs to this file (plain text, no color)",
    )
    log_group.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI color output",
    )

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    cfg = Config(
        output_path=args.output,
        session_dir=args.session_dir,
        audio_codec=args.codec,
        audio_format=args.audio_format,
        scroll_sleep=args.scroll_sleep,
        stuck_threshold=args.stuck_threshold,
        headless=args.headless,
        dry_run=args.dry_run,
        log_level=args.log_level,
        log_file=args.log_file,
        no_color=args.no_color,
        playlist_url=args.url,
        export_kopuz_playlist=args.export_kopuz_playlist,
    )
    cfg.setup_logger()

    print_banner(cfg)

    if cfg.dry_run:
        cfg.logger.warning("DRY-RUN mode — no audio will be downloaded")

    scraper = Scraper(cfg)
    scraper.start()


if __name__ == "__main__":
    main()


# ---------------------------------------------
#  FUN FACT — Spotify image URL anatomy
# ---------------------------------------------
#
#  Fixed prefix:  ab67616d0000
#  Resolution:    1e02  → high-res  |  4851  → thumbnail
#  Track hash:    e.g. b1f8da74f225fa1225cdface
#
#  Examples:
#   time2time  BIG   ab67616d00001e025bd08cab85dcbf82cc726c1c
#   time2time  SMALL ab67616d000048515bd08cab85dcbf82cc726c1c
#   505        BIG   ab67616d00001e02b1f8da74f225fa1225cdface
#   505        SMALL ab67616d00004851b1f8da74f225fa1225cdface
#
#  Custom/playlist photo prefix:  ab67706c0000d72
#  Artist photo prefix:           ab6761610000
#  User photo prefix:             ab6775700000
