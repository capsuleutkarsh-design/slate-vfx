"""
What is on a client drive, worked out once.

Build & Ingest used to walk the drive three times - once on the UI thread for
the stitch check (freezing the window on a network drive), then twice more in
the worker to count and size it - and each walk had its own idea of a shot.
The survey walks it once, in the background, and everything after it uses the
same answer: the stitch dialog, the pre-flight summary a coordinator approves,
and the run itself. What is shown before the run is what the run does.

It also settles the questions the run used to settle silently:

* **Which reel.** The nearest folder above a shot that looks like a reel
  (REEL_03, R2, EP01), else the first folder under the drive - never just the
  shot's parent, which filed REEL_03/seq_A/sub/SH_100 under a reel called "sub".
* **What the shot is called.** A clean name is kept; 'sh 060 (client)' is
  proposed as SH_060 (editable before anything copies), and a client's
  version tail (SH_040_v02) is kept as the client version instead of dropped.
* **What a shot is** (shot_folder_of). The folder that holds the plates -
  unless it is named after a format or a size (SH_010/EXR, SH_010/MOV,
  SH_010/4K): then the shot is the folder above it, and EXR and MOV are two
  formats of one shot, never two shots called 'EXR' and 'MOV'. The whole
  drive is looked at, however deep.
* **What is not a shot** (is_client_material). Paperwork, LUTs, references,
  audio and EDLs - a LUTS or REFERENCE folder, a folder of nothing but
  paperwork, loose files at the top of the drive - are client material, filed
  under the client folder (client_folder_for), not shots. Empty folders and
  system junk (Thumbs.db, .DS_Store, *.tmp) are listed so the report can say so.
* **What clashes.** With a Target Reel override, SH_010 from REEL_A and SH_010
  from REEL_B would have merged into one shot; they are given distinct names.
* **What is already in the project.** A shot whose files are identical to a
  scan version already ingested is marked unchanged, so running the same
  drive again (Copy leaves it full) does not add v002, v003 ... of the same
  plates.
"""

from __future__ import annotations

import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from slate.core.domain.naming import name_problem, normalise_shot_name, shot_name_problem
from slate.core.domain.stitch_detect import StitchGroup, group_by_reel

# --- what is not media -------------------------------------------------------
IGNORED_FILES = {'.ds_store', 'thumbs.db', 'desktop.ini', '$recycle.bin',
                 'system volume information'}
IGNORED_SUFFIXES = {'.tmp', '.bak', '.swp', '.crdownload', '.partial'}

# What comes with a delivery besides plates: paperwork, LUTs and colour
# decisions, audio, edit lists. A folder of nothing but these is not a shot;
# they are filed under the client folder (inside a shot they travel with it).
DOCUMENT_EXTENSIONS = {
    '.pdf', '.txt', '.doc', '.docx', '.xls', '.xlsx', '.csv', '.rtf', '.md',
    '.odt', '.ods', '.ppt', '.pptx', '.htm', '.html', '.eml', '.msg', '.pages',
    '.numbers', '.key', '.json', '.xml',
    '.cube', '.3dl', '.lut', '.csp', '.cc', '.ccc', '.cdl', '.clf',
    '.wav', '.aif', '.aiff', '.mp3', '.m4a', '.bwf',
    '.edl', '.aaf', '.otio', '.ale', '.fcpxml',
}

# Folders that hold client material, whatever is in them (REFERENCE/look.jpg).
MATERIAL_FOLDERS = {
    'lut', 'luts', 'cdl', 'cdls', 'ref', 'refs', 'reference', 'references', 'audio',
    'sound', 'edl', 'edls', 'doc', 'docs', 'documents', 'notes', 'paperwork',
}

# Folders named after a format or a size hold a shot's plates; they are not
# the shot (SH_010/EXR, SH_010/MOV, SH_010/EXR/4K).
FORMAT_FOLDERS = {
    'exr', 'exrs', 'dpx', 'tif', 'tiff', 'jpg', 'jpeg', 'png', 'tga', 'cin', 'dng', 'hdr',
    'ari', 'arri', 'r3d', 'braw', 'mov', 'mp4', 'mxf', 'avi', 'qt', 'quicktime', 'prores',
    'dnxhd', 'dnxhr', 'h264', 'plate', 'plates', 'scan', 'scans', 'proxy', 'proxies',
    'full', 'fullres', 'half', 'hires', 'lores', 'frames', 'images', 'video',
}
_SIZE_FOLDER = re.compile(r"^(\d+k|\d{3,5}x\d{3,5}|\d{3,4}p)$", re.IGNORECASE)

# Where client deliveries (documents, LUTs, the ingest reports) go in a
# project when the template does not say.
CLIENT_FOLDER = "01_Frm Client"
_CLIENT_NAMES = ("01_frm client", "01_from client", "01_client")

INCOMING_REEL = "Reel_Incoming"
_REEL_NAME = re.compile(r"^(?:reel|rl|r|ep|episode)[ _\-]?\d+(?:\D.*)?$", re.IGNORECASE)
_RESCAN_TAIL = re.compile(r"[_\-](scan[_]?[a-z0-9]*|rescan)$", re.IGNORECASE)
_VERSION_TAIL = re.compile(r"[_\-](v\d+)$", re.IGNORECASE)
_SCAN_VERSION = re.compile(r"v(\d+)", re.IGNORECASE)

# Windows' classic path limit. Slate copies past it; Explorer and some tools
# still cannot open such a file, so the pre-flight says so.
LONG_PATH = 259


def is_junk_file(path) -> bool:
    """Files that must never be treated as delivered media."""
    name = Path(path).name.lower()
    if name in IGNORED_FILES or name.startswith("._") or name.startswith("~$"):
        return True
    return Path(name).suffix in IGNORED_SUFFIXES


def is_document(path) -> bool:
    """Client material by its extension (paperwork, LUT, audio, edit list)."""
    return Path(path).suffix.lower() in DOCUMENT_EXTENSIONS


def is_format_folder(name: str) -> bool:
    """A folder named after a format or a size (EXR, MOV, 4K, 1920x1080)."""
    text = str(name or "").strip().lower()
    return text in FORMAT_FOLDERS or bool(_SIZE_FOLDER.match(text))


def shot_folder_of(folder: Path, source: Path) -> Path:
    """
    The shot a folder of plates belongs to: the folder itself, or the first
    folder above it that is not named after a format (SH_010/EXR -> SH_010).
    The drive folder itself when every folder up to it is a format folder.
    """
    folder, source = Path(folder), Path(source)
    while folder != source and is_format_folder(folder.name):
        folder = folder.parent
    return folder


def is_client_material(folder: Path, source: Path) -> bool:
    """Whether everything in this folder is client material (a LUTS or REFERENCE folder, at any depth)."""
    try:
        parts = Path(folder).relative_to(Path(source)).parts
    except ValueError:
        return False
    return any(part.strip().lower() in MATERIAL_FOLDERS for part in parts)


def client_folder_for(template=None) -> str:
    """
    The one rule for where client deliveries go in a project: documents and
    other client material, and the ingest reports. The template's own
    'client_folder' when it names one, else its base folder called
    01_Frm Client (or 01_From Client, 01_Client), else 01_Frm Client.
    """
    info = template if isinstance(template, dict) else {}
    structure = info.get("structure") if isinstance(info.get("structure"), dict) else info
    chosen = str(structure.get("client_folder") or info.get("client_folder") or "").strip()
    if chosen:
        return chosen
    base = structure.get("base_folders") or []
    return next((b for b in base if str(b).strip().lower() in _CLIENT_NAMES), CLIENT_FOLDER)


def split_shot_name(folder_name: str) -> Tuple[str, str, str]:
    """
    (base, rescan tag, client version) of a delivered folder name.

    'SH050_ScanB' -> ('SH050', 'ScanB', ''), 'SH_040_v02' -> ('SH_040', '', 'v02').
    The same rule the run has always used to put two deliveries of a shot
    into one shot with two scan versions.
    """
    name = str(folder_name or "")
    rescan = ""
    match = _RESCAN_TAIL.search(name)
    if match:
        rescan = match.group(1)
        name = name[:match.start()]
    version = ""
    match = _VERSION_TAIL.search(name)
    if match:
        version = match.group(1)
        name = name[:match.start()]
    return (name or str(folder_name or "")), rescan, version


def reel_for(shot_path: Path, source: Path) -> str:
    """The reel a shot folder belongs to (see the module notes)."""
    try:
        parts = Path(shot_path).relative_to(Path(source)).parts[:-1]
    except ValueError:
        parts = ()
    if not parts:
        return INCOMING_REEL
    for part in reversed(parts):
        if _REEL_NAME.match(part):
            return part
    return parts[0]


# --- the survey ---------------------------------------------------------------
@dataclass
class SurveyFile:
    path: Path
    size: int = 0
    mtime: float = 0.0
    sub: str = ""                 # the folder below the shot it came from ('EXR/4K'), '' when none


@dataclass
class SurveyShot:
    source: Path                  # the folder on the drive
    source_name: str              # as delivered
    reel: str                     # where it goes
    source_reel: str              # where it was on the drive
    base: str                     # the name with rescan / version tails taken off
    name: str                     # the shot it becomes (editable before the run)
    proposed: str = ""            # what Slate proposed for `name`
    rescan: str = ""              # 'ScanB'
    client_version: str = ""      # 'v02'
    files: List[SurveyFile] = field(default_factory=list)
    is_root: bool = False         # loose media at the top of the drive
    skip: bool = False            # not brought in this run
    unchanged_from: str = ""      # identical to this scan version already in the project
    notes: List[str] = field(default_factory=list)
    # Two format folders of this shot hold files of the same name (EXR/2K and
    # EXR/4K): each file keeps its folder inside the scan version.
    keep_subfolders: bool = False
    attention: bool = False       # Slate could not read the name; the pre-flight marks it

    @property
    def tail(self) -> str:
        """What came off the delivered name (the part of a stitch, or the rescan)."""
        return "_".join(t for t in (self.rescan, self.client_version) if t)

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    def signature(self) -> List[Tuple[str, int, float]]:
        return [(f.path.name.lower(), f.size, f.mtime) for f in self.files]


@dataclass
class IngestSurvey:
    source: Path
    target_reel: str = ""
    shots: List[SurveyShot] = field(default_factory=list)
    documents: List[SurveyFile] = field(default_factory=list)
    empty_folders: List[str] = field(default_factory=list)
    junk_files: List[str] = field(default_factory=list)
    stitch_groups: List[StitchGroup] = field(default_factory=list)
    structure_only: bool = False  # no files anywhere: build the folders from the names
    documents_filed_before: List[SurveyFile] = field(default_factory=list)
    # Media loose at the top of the drive: filed with the documents unless the
    # coordinator makes it a shot in the pre-flight (loose_media_as_shot).
    loose_media: List[SurveyFile] = field(default_factory=list)
    unreadable: List[str] = field(default_factory=list)
    cancelled: bool = False
    seconds: float = 0.0

    # -- totals of what will actually be brought in
    def active_shots(self) -> List[SurveyShot]:
        return [s for s in self.shots if not s.skip]

    @property
    def total_files(self) -> int:
        return sum(s.file_count for s in self.active_shots()) + len(self.documents)

    @property
    def total_bytes(self) -> int:
        return sum(s.size for s in self.active_shots()) + sum(d.size for d in self.documents)

    def destination_shots(self, stitch_mapping: Dict = None) -> List[Tuple[str, str]]:
        """
        The shots this delivery becomes: one per (reel, destination name).
        Stitch parts are one shot; two deliveries of a shot (ScanA, ScanB)
        are one shot with two scan versions. The pre-flight, the result and
        the dashboard all count this.
        """
        seen = []
        for shot in self.active_shots():
            key = (shot.reel, self.destination_of(shot, stitch_mapping))
            if key not in seen:
                seen.append(key)
        return seen

    @property
    def reels(self) -> List[str]:
        seen = []
        for shot in self.active_shots():
            if shot.reel not in seen:
                seen.append(shot.reel)
        return seen

    # -- after the stitch question
    @staticmethod
    def stitched_name(shot: "SurveyShot", stitch_mapping: Dict = None) -> Optional[str]:
        """The merged shot this folder is a part of, or None."""
        mapping = stitch_mapping or {}
        for key in ((shot.reel, shot.source_name), (shot.reel, shot.base), (shot.reel, shot.name),
                    shot.source_name, shot.base):
            if key in mapping:
                return mapping[key]
        return None

    def destination_of(self, shot: "SurveyShot", stitch_mapping: Dict = None) -> str:
        return self.stitched_name(shot, stitch_mapping) or shot.name

    def name_clashes(self, stitch_mapping: Dict = None) -> Dict[str, List["SurveyShot"]]:
        """
        Destination shots that unrelated source folders would share:
        {'REEL_01/SH_0120': [the shots]}, spelled as the first one is.
        """
        owners: Dict[Tuple[str, str], set] = {}
        members: Dict[Tuple[str, str], List[SurveyShot]] = {}
        shown: Dict[Tuple[str, str], str] = {}
        for shot in self.active_shots():
            dest = self.destination_of(shot, stitch_mapping)
            # Rescans of one shot (siblings SH_050_ScanA, SH_050_ScanB) and the
            # parts of a stitch share it on purpose. Anything else - another
            # folder, another sequence - is a different shot.
            if self.stitched_name(shot, stitch_mapping):
                who = ("stitch",)
            else:
                who = (str(shot.source.parent).lower(), shot.base.lower())
            key = (shot.reel.lower(), dest.lower())
            owners.setdefault(key, set()).add(who)
            members.setdefault(key, []).append(shot)
            shown.setdefault(key, f"{shot.reel}/{dest}")
        return {shown[key]: members[key] for key, who in owners.items() if len(who) > 1}

    def problems(self, stitch_mapping: Dict = None) -> List[str]:
        """Reasons the run cannot start as it stands (bad or clashing names)."""
        out = []
        for shot in self.active_shots():
            # The dashboard's shot rule, so registration never refuses a shot
            # whose folders were already built.
            problem = shot_name_problem(shot.name, f"{shot.source_name}: the name")
            if problem:
                out.append(problem)
        for clash in self.name_clashes(stitch_mapping):
            out.append(f"Two different folders would both become {clash}. Rename one of them.")
        return out

    def loose_media_as_shot(self, on: bool) -> None:
        """
        Media loose at the top of the drive: filed with the documents (the
        default - a reference, a LUT, a temp sound), or brought in as one shot
        named after the drive when the coordinator says it is one.
        """
        root = [s for s in self.shots if s.is_root]
        if on and not root:
            loose = {id(f) for f in self.loose_media}
            files = [d for d in self.documents if id(d) in loose]
            if not files:
                return
            self.documents = [d for d in self.documents if id(d) not in loose]
            name = normalise_shot_name(self.source.name) or "Incoming"
            self.shots.append(SurveyShot(
                source=self.source, source_name=self.source.name, reel=self.target_reel or INCOMING_REEL,
                source_reel=INCOMING_REEL, base=self.source.name, name=name, proposed=name,
                files=files, is_root=True, notes=["Loose files at the top of the drive"]))
        elif not on and root:
            self.shots = [s for s in self.shots if not s.is_root]
            for shot in root:
                self.documents.extend(shot.files)

    def long_paths(self, project_path: Path, reels_root: Path = None, scan_root: str = "01_Scan") -> int:
        """How many files would land at a path past Windows' 260 characters."""
        reels_root = Path(reels_root) if reels_root else Path(project_path) / "05_Reels"
        count = 0
        for shot in self.active_shots():
            base = len(str(reels_root / shot.reel / shot.name / scan_root / "v001" / "EXR"))
            count += sum(1 for f in shot.files if base + 1 + len(f.path.name) > LONG_PATH)
        return count

    def mark_unchanged(self, reels_root: Path, scan_root: str = "01_Scan",
                       stitch_mapping: Dict = None) -> int:
        """
        Skip shots whose files are already in the project, identical to a scan
        version that is there (same names, same sizes). Returns how many.
        """
        groups: Dict[Tuple[str, str], List[SurveyShot]] = {}
        for shot in self.shots:
            if not shot.files:
                continue
            groups.setdefault((shot.reel, self.destination_of(shot, stitch_mapping)), []).append(shot)

        skipped = 0
        for (reel, dest), members in groups.items():
            versions = existing_versions(Path(reels_root) / reel / dest / scan_root)
            if not versions:
                continue
            stitched = any(self.destination_of(s, stitch_mapping) != s.name for s in members)
            candidates = [members] if stitched else [[m] for m in members]
            for group in candidates:
                incoming = []
                for member in group:
                    incoming.extend(member.signature())
                for version, have in versions.items():
                    if incoming and same_files(incoming, have):
                        for member in group:
                            member.unchanged_from = version
                            member.skip = True
                            skipped += 1
                        break
        return skipped


# Copies keep their modification time; FAT-formatted client drives store it
# to the nearest two seconds.
MTIME_TOLERANCE = 2.0


def same_files(incoming, existing) -> bool:
    """
    Whether two lists of (name, size, modified time) are the same files.

    The time matters: a re-grade of a DPX plate has the same names and the
    same sizes as the grade before it - only the time tells them apart. A copy
    Slate made keeps the client file's time.
    """
    if len(incoming) != len(existing):
        return False
    pool: Dict[Tuple[str, int], List[float]] = {}
    for name, size, mtime in existing:
        pool.setdefault((name, size), []).append(mtime)
    for name, size, mtime in incoming:
        times = pool.get((name, size))
        if not times:
            return False
        match = next((t for t in times if abs(t - mtime) <= MTIME_TOLERANCE), None)
        if match is None:
            return False
        times.remove(match)
    return True


def mark_documents_filed(survey: "IngestSurvey", client_dir: Path) -> int:
    """
    Leave out documents an earlier run already filed (same name, size and
    time in one of the client folder's *_docs folders). Returns how many.
    """
    try:
        from slate.core.infra.file_operations import long_path
        folders = [d for d in Path(long_path(client_dir)).iterdir() if d.is_dir() and d.name.endswith("_docs")]
    except OSError:
        return 0
    keep, filed = [], []
    for doc in survey.documents:
        try:
            relative = doc.path.relative_to(survey.source)
        except ValueError:
            relative = Path(doc.path.name)
        match = False
        for folder in folders:
            try:
                stat = os.stat(str(folder / relative))
            except OSError:
                continue
            if stat.st_size == doc.size and abs(stat.st_mtime - doc.mtime) <= MTIME_TOLERANCE:
                match = True
                break
        (filed if match else keep).append(doc)
    survey.documents = keep
    survey.documents_filed_before.extend(filed)
    return len(filed)


def existing_versions(scan_dir: Path) -> Dict[str, List[Tuple[str, int, float]]]:
    """{'v001': [(file name, size, modified time)]} for each scan version in a shot."""
    out: Dict[str, List[Tuple[str, int, float]]] = {}
    # Read through the long-path form: a shot deep in a project is past 260
    # characters, and without it its versions looked empty - so every re-run
    # copied it again as a new scan version.
    from slate.core.infra.file_operations import long_path
    try:
        children = [c for c in Path(long_path(scan_dir)).iterdir()
                    if c.is_dir() and _SCAN_VERSION.fullmatch(c.name)]
    except OSError:
        return out
    for version in children:
        found = []
        for root, _dirs, files in os.walk(str(version)):
            for name in files:
                if is_junk_file(name):
                    continue
                try:
                    stat = os.stat(os.path.join(root, name))
                except OSError:
                    continue
                found.append((name.lower(), stat.st_size, stat.st_mtime))
        if found:
            out[version.name] = found
    return out


def _fallback_shots(source: Path) -> List[Path]:
    """Folder-only layout when there are no files at all: reel/shot or shot folders."""
    try:
        level1 = [d for d in source.iterdir() if d.is_dir()]
    except OSError:
        return []
    shots = []
    for reel_dir in level1:
        try:
            shots.extend(d for d in reel_dir.iterdir() if d.is_dir())
        except OSError:
            continue
    return shots or level1


def survey_drive(source, target_reel: str = "",
                 should_stop: Callable[[], bool] = None,
                 progress: Callable[[int, str], None] = None) -> IngestSurvey:
    """
    Walk the client drive once and describe everything on it.

    `should_stop` is polled between folders (the survey of a slow share can be
    cancelled); `progress(files_seen, folder)` is called now and then.
    """
    started = time.monotonic()
    source = Path(source)
    survey = IngestSurvey(source=source, target_reel=str(target_reel or "").strip())
    stop = should_stop or (lambda: False)

    shot_dirs: Dict[Path, List[SurveyFile]] = {}
    doc_dirs: Dict[Path, List[SurveyFile]] = {}
    seen = 0
    last_tell = 0.0

    def walk_error(exc):
        survey.unreadable.append(str(getattr(exc, "filename", "") or exc))

    # The whole drive, however deep: a cap left plates below it behind and
    # called their folder empty.
    for root, dirs, files in os.walk(source, onerror=walk_error):
        if stop():
            survey.cancelled = True
            break
        here = Path(root)
        dirs.sort(key=str.lower)
        material = is_client_material(here, source)
        media, docs = [], []
        for name in sorted(files, key=str.lower):
            path = here / name
            if is_junk_file(name):
                survey.junk_files.append(str(path.relative_to(source)))
                continue
            try:
                stat = os.stat(path)
                size, mtime = stat.st_size, stat.st_mtime
            except OSError:
                size, mtime = 0, 0.0
            entry = SurveyFile(path, size, mtime)
            (docs if material or is_document(name) else media).append(entry)
            seen += 1
        if here == source:
            # Loose at the top of the drive: client material, not a shot
            # named after the drive (the pre-flight can make it one).
            survey.loose_media.extend(media)
            survey.documents.extend(docs + media)
        elif media:
            shot = shot_folder_of(here, source)
            sub = here.relative_to(shot).as_posix() if here != shot else ""
            for entry in media + docs:          # paperwork inside a shot travels with it
                entry.sub = sub
            shot_dirs.setdefault(shot, []).extend(media + docs)
        elif docs:
            doc_dirs[here] = docs
        elif not dirs:
            survey.empty_folders.append(str(here.relative_to(source)))
        if progress and time.monotonic() - last_tell > 0.2:
            last_tell = time.monotonic()
            try:
                progress(seen, str(here))
            except Exception:  # pragma: no cover - progress never stops a survey
                pass

    for folder, docs in doc_dirs.items():
        if folder in shot_dirs and not is_client_material(folder, source):
            shot_dirs[folder].extend(docs)       # SH_010/notes.pdf beside SH_010/EXR
        else:
            survey.documents.extend(docs)

    entries: List[Tuple[Path, List[SurveyFile], bool]] = [(d, f, False) for d, f in shot_dirs.items()]

    if not entries and not survey.documents and not survey.cancelled:
        # Nothing to copy at all: the drive is a folder skeleton. Build the
        # shots from the folder names, as the ingest always has.
        survey.structure_only = True
        entries = [(d, [], False) for d in _fallback_shots(source)]
        survey.empty_folders = []

    for folder, shot_files, is_root in entries:
        source_reel = reel_for(folder, source) if not is_root else INCOMING_REEL
        base, rescan, version = split_shot_name(folder.name or "Incoming")
        proposed = normalise_shot_name(base) or "Incoming"
        shot = SurveyShot(
            source=folder, source_name=folder.name, reel=survey.target_reel or source_reel,
            source_reel=source_reel, base=base, name=proposed, proposed=proposed,
            rescan=rescan, client_version=version, files=list(shot_files), is_root=is_root,
        )
        if proposed != base:
            shot.notes.append(f"Name tidied from '{base}'")
        if base and not any(c.isascii() and c.isalpha() for c in base) and any(c.isalpha() for c in base):
            shot.attention = True
            shot.notes.append("Name spelled out in Latin letters by Slate - check it")
        names = Counter(f.path.name.lower() for f in shot.files)
        if any(n > 1 for n in names.values()):
            shot.keep_subfolders = True
            shot.notes.append("Same file names in " + ", ".join(sorted({f.sub or '.' for f in shot.files}))
                              + " - each keeps its folder")
        survey.shots.append(shot)

    survey.shots.sort(key=lambda s: (s.reel.lower(), s.name.lower(), s.source_name.lower()))
    _separate_clashes(survey)
    survey.stitch_groups = stitch_groups(survey)
    survey.seconds = time.monotonic() - started
    return survey


def _separate_clashes(survey: IngestSurvey) -> None:
    """
    With a Target Reel override, the same shot name from two source reels
    would merge into one shot. Each is given its source reel as a prefix
    instead (editable in the pre-flight).
    """
    if not survey.target_reel:
        return
    by_name: Dict[str, set] = {}
    for shot in survey.shots:
        by_name.setdefault(shot.name.lower(), set()).add(shot.source_reel.lower())
    for shot in survey.shots:
        if len(by_name.get(shot.name.lower(), ())) > 1:
            renamed = normalise_shot_name(f"{shot.source_reel}_{shot.name}")
            shot.notes.append(f"{shot.name} is also in another reel - renamed {renamed}")
            shot.name = shot.proposed = renamed


def stitch_groups(survey: IngestSurvey) -> List[StitchGroup]:
    """
    Stitch suggestions, per reel, from the shot names *after* rescan and
    version tails are taken off - so SH_050_ScanA and SH_050_ScanB are two
    deliveries of one shot, never two parts of a stitch.
    """
    by_reel: Dict[str, List[str]] = {}
    for shot in survey.shots:
        if shot.is_root:
            continue
        names = by_reel.setdefault(shot.reel, [])
        if shot.name not in names:
            names.append(shot.name)
    return group_by_reel(by_reel)


def apply_names(survey: IngestSurvey, names: Dict[int, str]) -> None:
    """Names a coordinator edited in the pre-flight, by row."""
    for index, name in (names or {}).items():
        if 0 <= index < len(survey.shots):
            survey.shots[index].name = str(name).strip()
