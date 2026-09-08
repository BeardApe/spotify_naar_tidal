"""
spotify_naar_tidal.py — Zet een Spotify-afspeellijst over naar een bestaande Tidal-playlist.

Wat dit doet:
1. Leest de tracks van de Spotify-lijst via de publieke embed-pagina
   (werkt ook voor Radio- en Mix-lijsten van Spotify zelf, waar de API nee op zegt;
   je hebt dus geen Spotify-sleutels nodig)
2. Zoekt elke track op in Tidal
3. Voegt de gevonden tracks toe aan je Tidal-playlist, zonder dubbels
4. Print op het einde wat niet gevonden werd

Lokaal uitvoeren (Windows):
    pip install -r requirements.txt
    python setup.py                      (eenmalig, maakt session.json)
    python spotify_naar_tidal.py

Andere lijsten? Geef ze mee als argument:
    python spotify_naar_tidal.py https://open.spotify.com/playlist/XXXX efc92d5f-7912-453b-9576-8a63bde1dd29

Via GitHub Actions: zie spotify_naar_tidal.yml (handmatig starten, lijst invullen, klaar).
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import tidalapi

# ── Standaardwaarden ─────────────────────────────────────────────
SPOTIFY_URL       = "https://open.spotify.com/playlist/37i9dQZF1E4k1jkpry5obv"
TIDAL_PLAYLIST_ID = "efc92d5f-7912-453b-9576-8a63bde1dd29"   # Ochtendjazz

# Waar de Tidal-sessie staat: lokaal session.json, in GitHub Actions /tmp/tidal_session.json
SESSION_PATHS = [Path("session.json"), Path("/tmp/tidal_session.json")]


# ── Spotify ──────────────────────────────────────────────────────
def spotify_playlist_id(url_of_id: str) -> str:
    """Haalt het ID uit een Spotify-link. Een los ID mag ook."""
    m = re.search(r"playlist[/:]([A-Za-z0-9]+)", url_of_id)
    return m.group(1) if m else url_of_id.strip()


def get_spotify_tracks(playlist_id: str) -> tuple[str, list[dict]]:
    """Leest naam en tracks van de Spotify-lijst via de embed-pagina."""
    url = f"https://open.spotify.com/embed/playlist/{playlist_id}"
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()

    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', resp.text, re.S)
    if not m:
        raise RuntimeError("Kon de trackgegevens niet vinden op de Spotify-pagina. Is de lijst publiek?")

    entity = json.loads(m.group(1))["props"]["pageProps"]["state"]["data"]["entity"]
    tracks = []
    for item in entity.get("trackList", []):
        title  = item.get("title", "").strip()
        artist = item.get("subtitle", "").split(",")[0].strip()   # eerste artiest volstaat om te zoeken
        if title and artist:
            tracks.append({"artist": artist, "title": title})
    return entity.get("name", playlist_id), tracks


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
    spotify_arg = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SPOTIFY_URL", SPOTIFY_URL)
    tidal_id    = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("TIDAL_PLAYLIST_ID", TIDAL_PLAYLIST_ID)

    print("Spotify-lijst lezen...")
    name, spotify_tracks = get_spotify_tracks(spotify_playlist_id(spotify_arg))
    print(f"  '{name}': {len(spotify_tracks)} tracks gevonden\n")

    print("Inloggen bij Tidal...")
    session  = load_tidal_session()
    playlist = session.playlist(tidal_id)
    existing = {str(t.id) for t in playlist.tracks()}
    print(f"  Playlist '{playlist.name}' heeft al {len(existing)} tracks\n")

    to_add, not_found = [], []
    for i, tr in enumerate(spotify_tracks, 1):
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
        # In stukken van 50 toevoegen, dat vindt Tidal prettiger
        for start in range(0, len(to_add), 50):
            playlist.add(to_add[start:start + 50])
        print(f"\n✓ {len(to_add)} tracks toegevoegd aan '{playlist.name}'")
    else:
        print("\nNiets toe te voegen, alles stond er al.")

    if not_found:
        print(f"\n{len(not_found)} tracks niet gevonden op Tidal:")
        for label in not_found:
            print(f"  - {label}")


if __name__ == "__main__":
    main()
