# TODO: Search genius for lines
# TODO: Pull results which match songs in spotify library
# TODO: Iterate line for parts of quote if full quote not available
# TODO: Return songs and parts of quote
import spotipy
import spotipy.util
import unidecode
from textdistance import levenshtein
import lyricsgenius

import os
import json
import re
import string
import random
import regex

from collections import namedtuple, defaultdict
from functools import lru_cache


CREDS_FILE = "creds.json"
REQUIRED_ARTIST_SCORE = 0.2
REQUIRED_SONG_SCORE = 0.3
EXCLUDED_GENIUS_TERMS = ["Songs That Reference Drugs"]
INSTR_ID = "07fdYUs00cdxyoXt7x23i1"
ANY_ARTIST = True

Track = namedtuple("Track", "name artist lyrics")

all_lyrics = []


def get_spotify(s_creds):
    """Get the spotify object from which to make requests."""
    # Authorize Spotify
    cache_path = "spotify/"
    try:
        os.mkdir(cache_path)
    except FileExistsError:
        pass
    except FileNotFoundError:
        os.mkdir("cache")
        os.mkdir(cache_path)

    token = spotipy.util.prompt_for_user_token(
        s_creds["username"],
        s_creds["scopes"],
        s_creds["client_id"],
        s_creds["client_secret"],
        s_creds["redirect_uri"],
        cache_path + "user.cache",
    )

    return spotipy.Spotify(auth=token)


def get_songs(results):
    """Create a songs list from results of a playlist search."""
    songs = []
    for result in results:
        track = result["track"]
        name = track["name"]
        artist = track["artists"][0]["name"].lower()
        songs.append(Track(name, artist, None))
    return songs


def get_library(spotify: spotipy.Spotify):
    """Load library from the users saved songs."""
    try:
        with open("artists.json", "r") as f:
            artists = set(json.load(f))
            artists_loaded = True
    except FileNotFoundError:
        artists_loaded = False
    try:
        with open("songs.json", "r") as f:
            songs = tuple(tuple(song) for song in json.load(f))
            songs_loaded = True
    except FileNotFoundError:
        songs_loaded = False

    if songs_loaded and artists_loaded:
        return artists, songs

    # 50 is max limit for api
    results = get_all(spotify, spotify.current_user_saved_tracks(limit=50))
    if not artists_loaded:
        artists = set()
    if not songs_loaded:
        songs = []
    for result in results:
        track = result["track"]
        artist = track["artists"][0]["name"].lower()
        if not artists_loaded:
            artists.add(artist)

    if not songs_loaded:
        songs = get_songs(results)
        results = get_all(spotify, spotify.user_playlist_tracks(None, INSTR_ID))
        inst_songs = get_songs(results)
        songs = tuple(song for song in songs if song not in inst_songs)

    with open("artists.json", "w") as f:
        json.dump(list(artists), f)
    with open("songs.json", "w") as f:
        json.dump(songs, f)

    return artists, tuple(songs)


def get_all(spotify: spotipy.Spotify, results: dict):
    """Grab more results until none remain."""
    items = results["items"]
    while results["next"]:
        results = spotify.next(results)
        items.extend(results["items"])

    return items


def remove_extra(name):
    """Remove the parentheses and hyphens from a song name."""
    return re.sub(r"-[\S\s]*", "", re.sub(r"\([\w\W]*\)", "", name))


@lru_cache
def clean(name, crop=True):
    """Remove potential discrepencies from the string."""
    if crop:
        name = remove_extra(name)
    name = unidecode.unidecode(name)  # Remove diacritics
    name = "".join(
        list(filter(lambda c: c in (string.ascii_letters + string.digits + " "), name))
    )
    name = name.lower().strip()
    return name


def distance(str1, str2):
    """Return the Needleman-Wunsch similarity between two strings."""
    return levenshtein.normalized_distance(str1, str2)


def match_artist(target, other):
    """Determine whether two artists match."""
    artist_name = clean(target)
    other_artist = clean(other)
    artist_dist = distance(artist_name, other_artist)
    if artist_dist > REQUIRED_ARTIST_SCORE:
        return False
    return True


def match(song, other):
    """Determine whether a song matches the result."""
    if not match_artist(song.artist, other.artist):
        return False

    song_name = clean(song.name)
    other_name = clean(other.name)
    song_dist = distance(song_name, other_name)
    if (
        song_dist <= REQUIRED_SONG_SCORE
        or song_name in other_name
        or other_name in song_name
    ):
        return True
    return False


def find_artist(artist, artists):
    """Search the entire library for matching artists."""
    for artist_ in artists:
        if match_artist(artist, artist_):
            return True
    return False


def find_phrase(phrase, lyrics):
    """Find parts of a phrase in lyrics."""
    match = regex.search(f"(?e)({phrase}){{d<=1,i<=1}}", lyrics)
    if match:
        print("*Found", phrase)
        return match[0]

    return False


@lru_cache
def search_song(genius, name, artist):
    """Search a song on Genius with caching."""
    return genius.search_song(name, artist)


@lru_cache
def find_word(genius, word, songs):
    """Find a single word from songs in library."""
    print("*Iterative search for", word)
    for num, song in enumerate(random.sample(songs, len(songs))):
        if num > 7:
            return [word]
        result = search_song(genius, song[0], song[1])
        try:
            lyrics = clean(result.lyrics.replace("\n", " "), False)
            if word in lyrics:
                print("*Found", word)
                return [Track(song[0], song[1], word)]
        except AttributeError:
            continue


def find_matches(genius, phrase, artists, songs):
    """Search matching songs for artists in library."""
    search_term = clean(phrase, False)
    print("*Searching for", search_term)

    for song in all_lyrics:
        matched = find_phrase(phrase, song.lyrics)
        if matched:
            return [Track(song.name, song.artist, matched)]

    results = genius.search_genius(phrase)
    for track in results["hits"]:
        artist = track["result"]["primary_artist"]["name"].lower()
        if artist in artists or find_artist(artist, artists) or ANY_ARTIST:
            name = track["result"]["title"]
            song = search_song(genius, name, artist)
            if not song:
                continue
            lyrics = clean(song.lyrics.replace("\n", " "), False)
            track = Track(name, artist, lyrics)
            all_lyrics.append(track)
            print("*Checking", track.name, track.artist)
            if search_term in track.lyrics:
                print("*Found", search_term)
                return [Track(track.name, track.artist, search_term)]
            else:
                print("*Searching phrase", search_term)
                matched = find_phrase(search_term, track.lyrics)
                if matched:
                    return [Track(track.name, track.artist, matched)]

    words = phrase.split(" ")
    if len(words) < 2:
        return find_word(genius, words[0], songs)

    head = " ".join(words[:len(words)//2])
    tail = " ".join(words[len(words)//2:])
    return [
        find_matches(genius, head, artists, songs),
        find_matches(genius, tail, artists, songs),
    ]


if __name__ == "__main__":
    with open(CREDS_FILE) as f:
        creds = json.load(f)

    sentence = input("Input sentence: ")
    spotify = get_spotify(creds["spotify"])
    artists, songs = get_library(spotify)

    genius = lyricsgenius.Genius(
        creds["genius"]["client access token"], excluded_terms=EXCLUDED_GENIUS_TERMS
    )
    matches = find_matches(genius, sentence, artists, songs)
    print(matches)