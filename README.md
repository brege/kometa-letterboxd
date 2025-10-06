## `kometa-letterboxd` - Complex Letterboxd Lists in Kometa Collections

The main command is

```bash
python letterboxd.py --config config.yml
```

Debian:

```bash
# Create an isolated environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Background

Useful kometa references, assuming [`/opt/kometa`](https://github.com/Kometa-Team/Kometa)

```bash
# /opt/kometa/modules/letterboxd.py
# /opt/kometa/defaults/chart/letterboxd.yml
# rg -c -g '*.py' letterboxd /opt/kometa/
```

Kometa's code is too hard to navigate and their documentation is very hard to use it as a reference.
It is not a reference. It reads like it's trying to be helpful by example, but then pattern formation gets difficult. It hops from niche examples to presumably common-knowledge.

I have spent more time trying to figure out this documentation than the unconventional and highly templated YAML configs.  It's not documentation. It's a discord log.

The output for a given 500 item library spans 10's of thousands of lines. I'm considering moving on from Kometa and just rolling my own special Letterboxd and mdblist. Aside from their nice poster art, yanking out the basic idea from the mdblist and potentially imdb list (Emmy's) it's all I really use.  Even the automatic streamer categories have unusual limitations.

This does not make it easy.
```bash
$ find . -type f -name "*.py" -not -path "./kometa-venv/*" -not -path "./.git/*" -exec wc -l {} \; | sort -n | tail -10
714 ./modules/overlays.py
920 ./modules/imdb.py
997 ./modules/util.py
1139 ./modules/cache.py
1200 ./modules/operations.py
1207 ./kometa.py
1381 ./modules/config.py
1936 ./modules/plex.py
2404 ./modules/meta.py
3765 ./modules/builder.py
```

### Repo Structure

```
.
├── collectors
│   ├── featured
│   │   └── showdown
│   │       ├── __init__.py         # main showdown job runner
│   │       ├── probe.py            # fetch and cache letterboxd's showdown dataset
│   │       └── storage.py          # how to effectively store (helper)
│   └── user
│       ├── dated.py                # special dated lists
│       ├── lists.py                # user lists
│       └── tagged.py               # tagged lists on letterboxd
├── common
│   ├── cache.py                    # json storage of letterboxd data (in general)
│   ├── kometa.py                   # kometa yaml collection builder
│   └── plex.py                     # purely an interface with plex
├── config.example.yml              # example config
├── config.yml                      # user config for kometa-letterboxd
├── data                            # all of this needs to be moved out of the repo root
│   ├── featured
│   │   └── showdown
│   │       ├── cache.json          # generated from letterboxd.com/showdown/ (30 minute run)
│   │       └── rotation.json       # showdown rotation state (sliding visibility window)
│   └── user
│       └── dated.json              # for "favorite movies - August, 2022" etc
├── letterboxd.py                   # main orchestrator
├── README.md                       # this file
└── requirements.txt                # plexapi, bs4, etc
```

### Usage

Better to use the configuration files, but here's a demonstration of the command line usage:
```bash
command examples
- python letterboxd.py --config config.yml
- python3 -m lists.showdown_plex \
     --config config.yml \
     --showdown-json showdown.json \
     --threshold 6 \
     --sort matches_desc \
     --window 5 \
     --label "Showdown Spotlight"
```

### How Showdowns Works

 run1: [P] is the frontpage collection [visible_{library,home,shared}=true
  run2: P is still discoverable in the collections tab, not on homepage
  [visible_{home,shared}=false;visible_library=true]
  run3 P: same as run 1, on its last day discoverable
  run4 p: no longer discoverable. delete this collection on plex (i think this
  https://kometa.wiki/en/latest/config/operations/?h=delete#delete-collections might help; i don't
  think it's the same key as build_colleciton: fals

|       |                                | |
| ----- | ------------------------------ |-|
| day 1 | abcdefghijklm[NO[P]QR]stuvwxyz | P `visible_*=true` |
| day 2 | abcdefghijklmn[OP[Q]RS]tuvwxyz | P `visible_library=true;visible_{home,shared}=false`  |
| day 3 | abcdefghijklmno[PQ[R]ST]uvwxyz | P same as last step, last day of being available |
| day 4 | abcdefghijklmnop[QR[S]TU]vwxyz | P -> p `delete_collection=true` |
| day 5 | abcdefghijklmnopq[RS[T]UV]wxyz | |

Dyslexic-friendly ascii.
```
  ===[--X--]====================
  ====[--X--]===================
  =====[--X--]==================

  window (5): [--X--]
  X: the visible on home page
  -: discoverable in collections
  =: all other collections w/ threshold >= 6
```
