import cloudscraper  # Added for Cloudflare bypass
from bs4 import BeautifulSoup
from urllib.parse import quote, urlparse
import json
import re

from dataclasses import dataclass, field
from .utils import FreetarError

# Updated to a more modern browser string
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"

# Create a global scraper session to reuse cookies/connection
scraper = cloudscraper.create_scraper()

@dataclass
class SearchResult:
    artist_name: str
    song_name: str
    tab_url: str
    artist_url: str
    _type: str
    version: int
    votes: int
    rating: float

    def __init__(self, data: dict):
        self.artist_name = data["artist_name"]
        self.song_name = data["song_name"]
        self.tab_url = urlparse(data["tab_url"]).path
        self.artist_url = data["artist_url"]
        self._type = data["type"]
        self.version = data["version"]
        self.votes = int(data["votes"])
        self.rating = round(data["rating"], 1)

    def __repr__(self):
        return f"{self.artist_name} - {self.song_name} (ver {self.version}) ({self._type} {self.rating}/5 - {self.votes} votes)"

@dataclass
class SongDetail:
    tab: str
    artist_name: str
    song_name: str
    version: int
    difficulty: str
    capo: str
    tuning: str
    tab_url: str
    alternatives: list[SearchResult] = field(default_factory=list)

    def __init__(self, data: dict):
        page_data = data["store"]["page"]["data"]
        self.tab = page_data["tab_view"]["wiki_tab"]["content"]
        self.artist_name = page_data["tab"]['artist_name']
        self.song_name = page_data["tab"]["song_name"]
        self.version = int(page_data["tab"]["version"])
        self._type = page_data["tab"]["type"]
        self.rating = int(page_data["tab"]["rating"])
        self.difficulty = page_data["tab_view"]["ug_difficulty"]
        self.appliciture = page_data["tab_view"]["applicature"]
        self.chords = []
        self.fingers_for_strings = []
        
        meta = page_data["tab_view"].get("meta")
        if isinstance(meta, dict):
            self.capo = meta.get("capo")
            _tuning = meta.get("tuning")
            self.tuning = f"{_tuning['value']} ({_tuning['name']})" if _tuning else None
        else:
            self.capo = None
            self.tuning = None
            
        self.tab_url = page_data["tab"]["tab_url"]
        self.alternatives = [
            SearchResult(alt) for alt in page_data["tab_view"]["versions"] 
            if alt.get("type", "") != "Official"
        ]
        self.fix_tab()

    def fix_tab(self):
        tab = self.tab.replace("\r\n", "<br/>").replace("\n", "<br/>").replace(" ", "&nbsp;")
        tab = tab.replace("[tab]", "").replace("[/tab]", "")
        tab = re.sub(r'\[ch\](?P<root>[A-Ha-h](#|b)?)(?P<quality>[^[/]+)?(?P<bass>/[A-Ha-h](#|b)?)?\[\/ch\]', self.parse_chord, tab)
        self.tab = tab

    def parse_chord(self, chord):
        root = f'<span class="chord-root">{chord.group("root")}</span>'
        quality = f'<span class="chord-quality">{chord.group("quality")}</span>' if chord.group('quality') else ''
        bass = f'/<span class="chord-bass">{chord.group("bass")[1:]}</span>' if chord.group('bass') else ''
        return f'<span class="chord fw-bold">{root + quality + bass}</span>'

@dataclass
class Search:
    results: list
    total_pages: int
    current_page: int

    def __init__(self, value: str, page: int):
        try:
            url = f"https://www.ultimate-guitar.com/search.php?page={page}&search_type=title&value={quote(value)}"
            # Use scraper instead of requests
            resp = scraper.get(url, headers={'User-Agent': USER_AGENT}, timeout=15)
            
            with open('/tmp/ug_debug.html', 'w') as f:
                f.write(resp.text)
            
            resp.raise_for_status()
            bs = BeautifulSoup(resp.text, 'html.parser')
            store_div = bs.find("div", {"class": "js-store"})
            
            if not store_div:
                raise FreetarError("Could not find the data store on the page. Check /tmp/ug_debug.html")

            data = json.loads(store_div.attrs['data-content'])
            self.results = self.get_results(data)
            
            pagination = data['store']['page']['data'].get('pagination', {})
            self.total_pages = pagination.get('total', 1)
            self.current_page = pagination.get('current', 1)
            
        except Exception as e:
            raise FreetarError(f"Search failed: {str(e)}")

    def get_results(self, data: dict):
        results = data['store']['page']['data'].get('results', [])
        return [SearchResult(r) for r in results if r.get("type") not in ("Pro", "Official", None)]

def get_chords(s: SongDetail) -> SongDetail:
    if s.appliciture is None:
        return dict(), dict()

    chords = {}
    fingerings = {}

    for chord in s.appliciture:
        for chord_variant in s.appliciture[chord]:
            frets = chord_variant["frets"]
            min_fret = min(frets)
            max_fret = max(frets)
            possible_frets = list(range(min_fret, max_fret+1))
            variants_temp = {
                possible_fret: [1 if b == possible_fret else 0 for b in frets][::-1]
                for possible_fret
                in possible_frets
                if possible_fret > 0
            }

            variants = dict()
            found = False
            for fret, fingers in variants_temp.items():
                try:
                    if not found and fingers.index(1) >= 0:
                        found = True
                except ValueError:
                    ...

                if found:
                    variants[fret] = fingers

            if not len(variants):
                continue
            while len(variants) < 6:
                variants[max(variants) + 1] = [0] * 6

            variant_strings_pressed = [*variants.values()]
            variant_strings_pressed = [sum(x) for x in zip(*variant_strings_pressed)]
            unstrummed_strings = [int(not bool(y)) for y in variant_strings_pressed]

            fingering_for_variant = []
            for finger, x in zip(chord_variant["fingers"][::-1], unstrummed_strings):
                fingering_for_variant.append("x" if x else finger)
            fingering_for_variant = fingering_for_variant

            if chord not in chords:
                chords[chord] = []
                fingerings[chord] = []
            chords[chord].append(variants)
            fingerings[chord].append(fingering_for_variant)

    return chords, fingerings

def ug_tab(url_path: str):
    try:
        url = "https://tabs.ultimate-guitar.com/tab/" + url_path
        resp = scraper.get(url, headers={'User-Agent': USER_AGENT}, timeout=15)
        resp.raise_for_status()
        
        bs = BeautifulSoup(resp.text, 'html.parser')
        data_div = bs.find("div", {"class": "js-store"})
        data = json.loads(data_div.attrs['data-content'])
        
        s = SongDetail(data)
        
        # This is the missing link that populates the chord diagrams
        s.chords, s.fingers_for_strings = get_chords(s)
        
        return s
    except Exception as e:
        raise FreetarError(f"Could not parse chord: {e}")
