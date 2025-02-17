# coding: utf-8
from __future__ import print_function, unicode_literals

# awk -F\" '/add_argument\("-[^-]/{print(substr($2,2))}' copyparty/__main__.py | sort | tr '\n' ' '
zs = "a c e2d e2ds e2dsa e2t e2ts e2tsr e2v e2vp e2vu ed emp i j lo mcr mte mth mtm mtp nb nc nid nih nw p q s ss sss v z zv"
onedash = set(zs.split())


def vf_bmap() -> dict[str, str]:
    """argv-to-volflag: simple bools"""
    ret = {
        "never_symlink": "neversymlink",
        "no_dedup": "copydupes",
        "no_dupe": "nodupe",
        "no_forget": "noforget",
        "dav_auth": "davauth",
        "dav_rt": "davrt",
    }
    for k in (
        "dotsrch",
        "e2t",
        "e2ts",
        "e2tsr",
        "e2v",
        "e2vu",
        "e2vp",
        "grid",
        "hardlink",
        "magic",
        "no_sb_md",
        "no_sb_lg",
        "rand",
        "xdev",
        "xlink",
        "xvol",
    ):
        ret[k] = k
    return ret


def vf_vmap() -> dict[str, str]:
    """argv-to-volflag: simple values"""
    ret = {}
    for k in ("lg_sbf", "md_sbf", "unlist"):
        ret[k] = k
    return ret


def vf_cmap() -> dict[str, str]:
    """argv-to-volflag: complex/lists"""
    ret = {}
    for k in ("dbd", "html_head", "mte", "mth", "nrand"):
        ret[k] = k
    return ret


permdescs = {
    "r": "read; list folder contents, download files",
    "w": 'write; upload files; need "r" to see the uploads',
    "m": 'move; move files and folders; need "w" at destination',
    "d": "delete; permanently delete files and folders",
    "g": "get; download files, but cannot see folder contents",
    "G": 'upget; same as "g" but can see filekeys of their own uploads',
}


flagcats = {
    "uploads, general": {
        "nodupe": "rejects existing files (instead of symlinking them)",
        "hardlink": "does dedup with hardlinks instead of symlinks",
        "neversymlink": "disables symlink fallback; full copy instead",
        "copydupes": "disables dedup, always saves full copies of dupes",
        "daw": "enable full WebDAV write support (dangerous);\nPUT-operations will now \033[1;31mOVERWRITE\033[0;35m existing files",
        "nosub": "forces all uploads into the top folder of the vfs",
        "magic": "enables filetype detection for nameless uploads",
        "gz": "allows server-side gzip of uploads with ?gz (also c,xz)",
        "pk": "forces server-side compression, optional arg: xz,9",
    },
    "upload rules": {
        "maxn=250,600": "max 250 uploads over 15min",
        "maxb=1g,300": "max 1 GiB over 5min (suffixes: b, k, m, g)",
        "rand": "force randomized filenames, 9 chars long by default",
        "nrand=N": "randomized filenames are N chars long",
        "sz=1k-3m": "allow filesizes between 1 KiB and 3MiB",
        "df=1g": "ensure 1 GiB free disk space",
    },
    "upload rotation\n(moves all uploads into the specified folder structure)": {
        "rotn=100,3": "3 levels of subfolders with 100 entries in each",
        "rotf=%Y-%m/%d-%H": "date-formatted organizing",
        "lifetime=3600": "uploads are deleted after 1 hour",
    },
    "database, general": {
        "e2d": "enable database; makes files searchable + enables upload dedup",
        "e2ds": "scan writable folders for new files on startup; also sets -e2d",
        "e2dsa": "scans all folders for new files on startup; also sets -e2d",
        "e2t": "enable multimedia indexing; makes it possible to search for tags",
        "e2ts": "scan existing files for tags on startup; also sets -e2t",
        "e2tsa": "delete all metadata from DB (full rescan); also sets -e2ts",
        "d2ts": "disables metadata collection for existing files",
        "d2ds": "disables onboot indexing, overrides -e2ds*",
        "d2t": "disables metadata collection, overrides -e2t*",
        "d2v": "disables file verification, overrides -e2v*",
        "d2d": "disables all database stuff, overrides -e2*",
        "hist=/tmp/cdb": "puts thumbnails and indexes at that location",
        "scan=60": "scan for new files every 60sec, same as --re-maxage",
        "nohash=\\.iso$": "skips hashing file contents if path matches *.iso",
        "noidx=\\.iso$": "fully ignores the contents at paths matching *.iso",
        "noforget": "don't forget files when deleted from disk",
        "dbd=[acid|swal|wal|yolo]": "database speed-durability tradeoff",
        "xlink": "cross-volume dupe detection / linking",
        "xdev": "do not descend into other filesystems",
        "xvol": "do not follow symlinks leaving the volume root",
        "dotsrch": "show dotfiles in search results",
        "nodotsrch": "hide dotfiles in search results (default)",
    },
    'database, audio tags\n"mte", "mth", "mtp", "mtm" all work the same as -mte, -mth, ...': {
        "mtp=.bpm=f,audio-bpm.py": 'uses the "audio-bpm.py" program to\ngenerate ".bpm" tags from uploads (f = overwrite tags)',
        "mtp=ahash,vhash=media-hash.py": "collects two tags at once",
    },
    "thumbnails": {
        "dthumb": "disables all thumbnails",
        "dvthumb": "disables video thumbnails",
        "dathumb": "disables audio thumbnails (spectrograms)",
        "dithumb": "disables image thumbnails",
    },
    "event hooks\n(better explained in --help-hooks)": {
        "xbu=CMD": "execute CMD before a file upload starts",
        "xau=CMD": "execute CMD after  a file upload finishes",
        "xiu=CMD": "execute CMD after  all uploads finish and volume is idle",
        "xbr=CMD": "execute CMD before a file rename/move",
        "xar=CMD": "execute CMD after  a file rename/move",
        "xbd=CMD": "execute CMD before a file delete",
        "xad=CMD": "execute CMD after  a file delete",
        "xm=CMD": "execute CMD on message",
    },
    "client and ux": {
        "grid": "show grid/thumbnails by default",
        "unlist": "dont list files matching REGEX",
        "html_head=TXT": "includes TXT in the <head>",
        "robots": "allows indexing by search engines (default)",
        "norobots": "kindly asks search engines to leave",
        "no_sb_md": "disable js sandbox for markdown files",
        "no_sb_lg": "disable js sandbox for prologue/epilogue",
        "sb_md": "enable js sandbox for markdown files (default)",
        "sb_lg": "enable js sandbox for prologue/epilogue (default)",
        "md_sbf": "list of markdown-sandbox safeguards to disable",
        "lg_sbf": "list of *logue-sandbox safeguards to disable",
    },
    "others": {
        "fk=8": 'generates per-file accesskeys,\nwhich will then be required at the "g" permission',
        "davauth": "ask webdav clients to login for all folders",
        "davrt": "show lastmod time of symlink destination, not the link itself\n(note: this option is always enabled for recursive listings)",
    },
}


flagdescs = {k.split("=")[0]: v for tab in flagcats.values() for k, v in tab.items()}
