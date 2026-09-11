"""
youtube_naar_tidal.py — Zet de tracklist uit een YouTube-beschrijving om naar een Tidal-playlist.

Wat dit doet:
1. Haalt titel en beschrijving van de YouTube-video op (via yt-dlp, geen API-sleutel nodig)
2. Pikt de tracklist eruit: regels zoals "00:00 Artiest - Titel", "1. Artiest – Titel" of "Artiest - Titel [Label]"
3. Zoekt elke track op in Tidal
4. Maakt een nieuwe Tidal-playlist met de naam van de video, of vult een bestaande aan (zonder dubbels)
5. Print op het einde wat niet gevonden werd

Lokaal uitvoeren (Windows):
    pip install yt-dlp
    python setup.py                      (eenmalig, maakt session.json — heb je al)
    python youtube_naar_tidal.py https://www.youtube.com/watch?v=XXXX

Wil je de tracks in een bestaande playlist zetten? Geef het Tidal-ID mee als tweede argument:
    python youtube_naar_tidal.py https://www.youtube.com/watch?v=XXXX efc92d5f-7912-453b-9576-8a63bde1dd29

Via GitHub Actions: zie youtube_naar_tidal.yml (handmatig starten, link invullen, klaar).
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

import tidalapi

# Waar de Tidal-sessie staat: lokaal session.json, in GitHub Actions /tmp/tidal_session.json
SESSION_PATHS = [Path("session.json"), Path("/tmp/tidal_session.json")]


# ── YouTube ──────────────────────────────────────────────────────
def get_youtube_info(url: str) -> tuple[str, str]:
    """Haalt titel en beschrijving op met yt-dlp. Geeft (titel, beschrijving)."""
    cmd = [sys.executable, "-m", "yt_dlp", "--skip-download", "--no-warnings",
           "--print", "%(title)s", "--print", "%(description)s", url]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp kon de video niet lezen:\n{result.stderr.strip()}")
    title, _, description = result.stdout.partition("\n")
    return title.strip(), description


# Wat vooraan een regel mag staan en genegeerd wordt: tijdcodes, nummering, streepjes, bullets
PREFIX = re.compile(
    r"^\s*[\(\[]?\d{1,2}:\d{2}(?::\d{2})?[\)\]]?\s*[-–—:]?\s*"   # 00:00 of (1:02:33)
    r"|^\s*\d{1,3}\s*[.)\-:]\s*"                                  # 1. of 12)
    r"|^\s*[-–—•*▪●]\s*"                                          # bullets
)
SPLIT = re.compile(r"\s+[-–—]\s+")   # scheiding artiest/titel
JUNK  = re.compile(r"subscribe|follow|instagram|facebook|tiktok|http|www\.|thanks|bedankt|merci|recorded|mixed by|tracklist", re.I)


def parse_tracklist(description: str) -> list[dict]:
    """Zoekt regels met 'Artiest - Titel' in de beschrijving. Geeft lijst van {'artist', 'title'}."""
    tracks = []
    for raw in description.splitlines():
        line = raw.strip()
        if not line or JUNK.search(line):
            continue
        # prefixen wegknippen, eventueel meerdere na elkaar ("1. 00:00 Artiest - Titel")
        prev = None
        while prev != line:
            prev, line = line, PREFIX.sub("", line, count=1)
        # labels en opmerkingen tussen haakjes achteraan weg: "[Ninja Tune]" "(1998)"
        line = re.sub(r"\s*[\[\(][^\]\)]*[\]\)]\s*$", "", line).strip()

        parts = SPLIT.split(line, maxsplit=1)
        if len(parts) != 2:
            continue
        artist, title = parts[0].strip(" \"'"), parts[1].strip(" \"'")
        # onbekende tracks ("ID - ID", "??? - ???") overslaan
        if not artist or not title or re.fullmatch(r"(id|\?+|unknown|unreleased)", artist, re.I) \
                or re.fullmatch(r"(id|\?+|unknown|unreleased)", title, re.I):
            continue
        if len(artist) > 60 or len(title) > 100:   # dat is geen track, dat is een zin
            continue
        # "feat." uit de artiestnaam halen, dat zoekt beter
        artist = re.split(r"\s+(?:feat\.?|ft\.?|featuring|&|x|,)\s+", artist, maxsplit=1, flags=re.I)[0]
        tracks.append({"artist": artist, "title": title})
    return tracks


# ── Tidal ────────────────────────────────────────────────────────
def load_tidal_session() -> tidalapi.Session:
    for path in SESSION_PATHS:
        if path.exists():
            session = tidalapi.Session()
            session.load_session_from_file(path)
            if session.check_login():
                return session
    raise RuntimeError("Geen geldige Tidal-sessie gevonden. Voer eerst 'python setup.py' uit.")


def clean(text: str) -> str:
    """Maakt titels vergelijkbaar: kleine letters, geen '(Remastered 2004)' of '- Live'."""
    text = re.sub(r"\(.*?\)|\[.*?\]|\s-\s.*$", "", text)
    return re.sub(r"[^a-z0-9]", "", text.lower())


def search_tidal_track(session: tidalapi.Session, artist: str, title: str):
    """Zoekt de track in Tidal. Geeft voorkeur aan een match op artiest én titel."""
    try:
        results = session.search(f"{artist} {title}", models=[tidalapi.media.Track], limit=10)
    except Exception as e:
        print(f"  [Tidal] Zoekfout '{artist} - {title}': {e}")
        return None

    candidates = results.get("tracks", [])
    want_artist, want_title = clean(artist), clean(title)

    # 1. artiest en titel kloppen allebei
    for t in candidates:
        if want_title == clean(t.name) and (want_artist in clean(t.artist.name) or clean(t.artist.name) in want_artist):
            return t
    # 2. artiest klopt
    for t in candidates:
        if want_artist in clean(t.artist.name) or clean(t.artist.name) in want_artist:
            return t
    return None


# ── Hoofdprogramma ───────────────────────────────────────────────
def main():
    youtube_url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("YOUTUBE_URL", "")
    tidal_id    = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("TIDAL_PLAYLIST_ID", "").strip()
    if not youtube_url:
        sys.exit("Geef een YouTube-link mee: python youtube_naar_tidal.py https://www.youtube.com/watch?v=XXXX")

    print("YouTube-beschrijving lezen...")
    video_title, description = get_youtube_info(youtube_url)
    yt_tracks = parse_tracklist(description)
    print(f"  '{video_title}': {len(yt_tracks)} tracks herkend in de beschrijving\n")
    if not yt_tracks:
        sys.exit("Geen tracklist gevonden. Staat ze wel in de beschrijving, in de vorm 'Artiest - Titel'?")

    print("Inloggen bij Tidal...")
    session = load_tidal_session()
    if tidal_id:
        playlist = session.playlist(tidal_id)
        existing = {str(t.id) for t in playlist.tracks()}
        print(f"  Playlist '{playlist.name}' heeft al {len(existing)} tracks\n")
    else:
        playlist = session.user.create_playlist(video_title[:100], f"Tracklist uit {youtube_url}")
        existing = set()
        print(f"  Nieuwe playlist gemaakt: '{playlist.name}'\n")

    to_add, not_found = [], []
    for i, tr in enumerate(yt_tracks, 1):
        label = f"{tr['artist']} - {tr['title']}"
        found = search_tidal_track(session, tr["artist"], tr["title"])
        if not found:
            print(f"  [{i:>2}] ✗ niet gevonden: {label}")
            not_found.append(label)
        elif str(found.id) in existing:
            print(f"  [{i:>2}] = staat er al:   {label}")
        else:
            print(f"  [{i:>2}] ✓ {found.artist.name} - {found.name}")
            to_add.append(found.id)
            existing.add(str(found.id))
        time.sleep(0.3)   # Tidal niet overvragen

    if to_add:
        for start in range(0, len(to_add), 50):
            playlist.add(to_add[start:start + 50])
        print(f"\n✓ {len(to_add)} tracks toegevoegd aan '{playlist.name}'")
    else:
        print("\nNiets toe te voegen.")

    if not_found:
        print(f"\n{len(not_found)} tracks niet gevonden op Tidal:")
        for label in not_found:
            print(f"  - {label}")


if __name__ == "__main__":
    main()
