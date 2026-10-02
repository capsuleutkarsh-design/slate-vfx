"""
CAP Rename without the screen: what each file would be called, whether that
can work, doing it, and undoing it.

Kept free of Qt so every rule here is tested directly - the old tests could
only read the worker's source text, which is how a rename that always crashed
shipped with green tests.

Three rules carry the weight:

* **The preview is the truth.** A row says "Will rename" only when the rename
  can really happen. Every file in a group that would land on one name is a
  conflict - not all but the first - and a name with a folder in it, a name
  Windows refuses or a name that is too long is refused with the reason.
* **The extension is left alone** unless the person says otherwise. Sanitize
  and re-pad act on the name before the extension; find and replace only
  reaches the extension when "Keep extension" is unticked, and a row whose
  extension changes says so.
* **Undo is a journal Slate replays**, kept in Slate's own folder - not a .bat
  dropped next to the plates. It is replayed in two phases (everything to a
  holding name, then back), so names that were swapped or shifted come back
  exactly; a .bat replaying moves one by one overwrote the first file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from slate.core.domain.naming import MAX_NAME_LENGTH, name_problem

logger = logging.getLogger(__name__)

WILL_RENAME = "Will rename"
UNCHANGED = "Unchanged"
CONFLICT = "Conflict"

MODE_REPLACE = "replace"
MODE_SEQUENCE = "sequence"


# ------------------------------------------------------------- ordering
_DIGITS = re.compile(r"(\d+)")


def natural_key(path) -> list:
    """IMG_2 before IMG_10: digits compare as numbers, text without case."""
    name = Path(str(path)).name
    return [int(part) if part.isdigit() else part.lower()
            for part in _DIGITS.split(name)]


def natural_sorted(paths: Iterable) -> List[Path]:
    return sorted((Path(p) for p in paths), key=natural_key)


# ------------------------------------------------------------- the rules
@dataclass
class RenameRules:
    mode: str = MODE_REPLACE
    # Find and replace
    search: str = ""
    replace: str = ""
    use_regex: bool = False
    case_sensitive: bool = False
    keep_extension: bool = True
    lowercase: bool = False
    sanitize: bool = False
    repad: bool = False
    repad_digits: int = 4
    # Number as a sequence
    base_name: str = ""
    start: int = 1001
    step: int = 1
    padding: int = 4


@dataclass
class RowPlan:
    source: Path
    new_name: str
    status: str = UNCHANGED
    reason: str = ""          # why a Conflict cannot rename
    warning: str = ""         # a Will rename worth a second look

    @property
    def target(self) -> Path:
        return self.source.parent / self.new_name

    @property
    def changes(self) -> bool:
        return self.status == WILL_RENAME


@dataclass
class RenamePlan:
    rows: List[RowPlan] = field(default_factory=list)
    pattern_error: str = ""   # the regular expression could not be used
    blocked: str = ""         # nothing can run until this is fixed ("Enter a base name")
    padding_needed: int = 0   # numbers outgrow the padding: this many digits fit

    @property
    def renames(self) -> List[Tuple[Path, Path]]:
        return [(row.source, row.target) for row in self.rows if row.changes]

    @property
    def conflicts(self) -> List[RowPlan]:
        return [row for row in self.rows if row.status == CONFLICT]

    @property
    def counts(self) -> Dict[str, int]:
        tally = Counter(row.status for row in self.rows)
        return {"files": len(self.rows), "rename": tally[WILL_RENAME],
                "conflict": tally[CONFLICT], "unchanged": tally[UNCHANGED],
                "warning": sum(1 for row in self.rows if row.warning)}

    @property
    def can_run(self) -> bool:
        return bool(self.renames) and not self.conflicts and not self.pattern_error and not self.blocked

    def why_not(self) -> str:
        """Why the Rename button is off, in one line - or '' when it is on."""
        if not self.rows:
            return "Load files first."
        if self.pattern_error:
            return f"The Find pattern is not valid: {self.pattern_error}"
        if self.blocked:
            return self.blocked
        conflicts = self.conflicts
        if conflicts:
            lines = [f"{row.source.name} - {row.reason}" for row in conflicts[:6]]
            more = f"\n... and {len(conflicts) - 6} more" if len(conflicts) > 6 else ""
            return f"{len(conflicts)} file(s) cannot be renamed:\n" + "\n".join(lines) + more
        if not self.renames:
            return "No names change with these rules."
        return ""


# ------------------------------------------------------------- name edits
def sanitize(text: str) -> str:
    """
    Spaces and dots become '_', anything that is not a letter, digit, '_' or
    '-' goes, in any script: 'प्लेट 01' -> 'प्लेट_01', 'shot 🎬 final' -> 'shot_final'.

    Letters include their accents and vowel signs (Unicode categories L, M,
    N); the old ASCII-only rule wiped Hindi names down to '_01'.
    """
    text = re.sub(r"[\s.]+", "_", str(text))
    kept = []
    for ch in text:
        if ch in "_-" or unicodedata.category(ch)[0] in "LMN":
            kept.append(ch)
    out = re.sub(r"_+", "_", "".join(kept))
    return out.strip("_")


def repad(text: str, digits: int) -> str:
    """The last run of digits written with `digits` digits: IMG_1 -> IMG_0001."""
    match = re.search(r"(\d+)(?!.*\d)", text)
    if not match:
        return text
    return text[:match.start()] + match.group(1).zfill(int(digits)) + text[match.end():]


def _split(name: str) -> Tuple[str, str]:
    path = Path(name)
    return path.stem, path.suffix


def _replace(text: str, rules: RenameRules) -> str:
    if not rules.search:
        return text
    flags = 0 if rules.case_sensitive else re.IGNORECASE
    pattern = rules.search if rules.use_regex else re.escape(rules.search)
    replacement = rules.replace if rules.use_regex else rules.replace.replace("\\", "\\\\")
    return re.sub(pattern, replacement, text, flags=flags)


def check_pattern(rules: RenameRules) -> str:
    """The regular expression's own complaint, or ''."""
    if rules.mode != MODE_REPLACE or not rules.search or not rules.use_regex:
        return ""
    try:
        compiled = re.compile(rules.search, 0 if rules.case_sensitive else re.IGNORECASE)
        # The replacement is parsed before anything is searched, so a group
        # that does not exist ('\2' with one group) is reported here too.
        compiled.sub(rules.replace, "")
    except (re.error, IndexError, ValueError) as exc:
        return str(exc)
    return ""


def _replace_name(name: str, rules: RenameRules) -> Tuple[str, str]:
    """(new name, warning) for find and replace mode."""
    stem, ext = _split(name)
    if rules.keep_extension:
        new = _replace(stem, rules)
        if rules.sanitize:
            new = sanitize(new)
        if rules.lowercase:
            new = new.lower()
        if rules.repad:
            new = repad(new, rules.repad_digits)
        return new + ext, ""

    whole = _replace(name, rules)
    new_stem, new_ext = _split(whole)
    if rules.sanitize:
        new_stem = sanitize(new_stem)
    if rules.lowercase:
        new_stem, new_ext = new_stem.lower(), new_ext.lower()
    if rules.repad:
        new_stem = repad(new_stem, rules.repad_digits)
    new = new_stem + new_ext
    warning = ""
    if new_ext.lower() != ext.lower():
        warning = f"The extension changes ({ext or 'none'} -> {new_ext or 'none'})."
    return new, warning


def plan(files: Iterable, rules: RenameRules) -> RenamePlan:
    """
    The proposed name of every file, and whether each rename can really work.
    """
    sources = [Path(f) for f in files]
    result = RenamePlan()

    error = check_pattern(rules)
    if error:
        # Nothing is proposed while the pattern is broken: the table shows the
        # names as they are and the reason sits under the Find field.
        result.pattern_error = error
        result.rows = [RowPlan(p, p.name) for p in sources]
        return result

    proposals: List[Tuple[Path, str, str]] = []
    if rules.mode == MODE_SEQUENCE:
        base = rules.base_name
        if not base.strip():
            result.blocked = "Enter a base name."
            result.rows = [RowPlan(p, p.name) for p in sources]
            return result
        last = rules.start + max(len(sources) - 1, 0) * rules.step
        if len(str(last)) > rules.padding:
            result.padding_needed = len(str(last))
        for index, path in enumerate(sources):
            number = str(rules.start + index * rules.step).zfill(rules.padding)
            warning = ""
            if result.padding_needed and len(number) > rules.padding:
                warning = (f"This number has {len(number)} digits, the others {rules.padding} - "
                           f"use {result.padding_needed} digits.")
            proposals.append((path, f"{base}{number}{path.suffix}", warning))
    else:
        for path in sources:
            try:
                new, warning = _replace_name(path.name, rules)
            except (re.error, IndexError) as exc:
                result.pattern_error = str(exc)
                result.rows = [RowPlan(p, p.name) for p in sources]
                return result
            proposals.append((path, new, warning))

    # Every name each row would end up with - a row that does not change
    # still holds its own name - so a collision is seen whole, before the
    # rows are judged one by one.
    claims = Counter(str(path.parent / new).lower() for path, new, _ in proposals)
    held = {str(p).lower() for p in sources}

    for path, new, warning in proposals:
        row = RowPlan(path, new, warning=warning)
        if new == path.name:
            row.status = UNCHANGED
            row.warning = ""
            result.rows.append(row)
            continue

        reason = name_problem(new, "A new name")
        if reason is None and len(new) > MAX_NAME_LENGTH:
            reason = f"Name too long ({len(new)} characters, max {MAX_NAME_LENGTH})."
        target = str(path.parent / new).lower()
        if reason is None and claims[target] > 1:
            reason = "Two or more files would take this name."
        if reason is None and (path.parent / new).exists() and target not in held:
            reason = "A file with this name is already in the folder."
        if reason:
            row.status, row.reason, row.warning = CONFLICT, reason, ""
        else:
            row.status = WILL_RENAME
            if rules.sanitize and not row.warning:
                before = len(_split(path.name)[0])
                after = len(_split(new)[0])
                if before and after < before / 2:
                    row.warning = "Sanitize removed more than half of this name."
        result.rows.append(row)
    return result


# ------------------------------------------------------------- doing it
@dataclass
class RenameOutcome:
    renamed: List[Tuple[Path, Path]] = field(default_factory=list)
    failed: List[Tuple[Path, str]] = field(default_factory=list)
    cancelled: bool = False

    @property
    def count(self) -> int:
        return len(self.renamed)


def _holding_name(path: Path) -> Path:
    return path.parent / f".slate_rename_{uuid.uuid4().hex}.tmp"


def rename_files(pairs: List[Tuple[Path, Path]],
                 should_stop: Callable[[], bool] = lambda: False,
                 progress: Callable[[int, int, str], None] = None) -> RenameOutcome:
    """
    Rename in two passes: every file to a holding name, then into place.

    One pass is order-dependent - renaming file 2 to what file 3 is called
    either clobbers file 3 or fails. Stopping during the first pass puts every
    staged file back under its own name, so a cancelled run changes nothing.
    A pair that would leave its folder is refused outright.
    """
    outcome = RenameOutcome()
    total = len(pairs)

    def tell(done, name):
        if progress:
            try:
                progress(done, total, name)
            except Exception:  # pragma: no cover - a progress hook never stops a rename
                pass

    staged: List[Tuple[Path, Path, Path]] = []
    for index, (old, new) in enumerate(pairs, start=1):
        old, new = Path(old), Path(new)
        if should_stop():
            outcome.cancelled = True
            break
        if old.parent.resolve() != new.parent.resolve():
            outcome.failed.append((old, "A rename cannot move a file to another folder."))
            continue
        if not old.exists():
            outcome.failed.append((old, "The file is no longer there."))
            continue
        holding = _holding_name(old)
        try:
            os.rename(old, holding)
            staged.append((holding, old, new))
        except OSError as exc:
            logger.warning("Could not stage %s: %s", old, exc)
            outcome.failed.append((old, exc.strerror or str(exc)))
        tell(index, old.name)

    if outcome.cancelled:
        for holding, old, _new in staged:
            try:
                os.rename(holding, old)
            except OSError as exc:  # pragma: no cover - would need a disk to vanish
                logger.error("Left %s staged as %s: %s", old.name, holding.name, exc)
        return outcome

    for holding, old, new in staged:
        try:
            if new.exists():
                raise FileExistsError(f"{new.name} appeared while renaming")
            os.rename(holding, new)
            outcome.renamed.append((old, new))
        except OSError as exc:
            logger.warning("Could not rename %s to %s: %s", old.name, new.name, exc)
            outcome.failed.append((old, getattr(exc, "strerror", None) or str(exc)))
            try:
                os.rename(holding, old)
            except OSError:  # pragma: no cover
                logger.error("Left %s staged as %s", old.name, holding.name)
        tell(total, new.name)
    return outcome


# ------------------------------------------------------------- the journal
JOURNAL_FOLDER = "rename_undo"


def journal_dir(app_dir=None) -> Path:
    """Where undo journals live: Slate's own folder, never beside the plates."""
    if app_dir is None:
        app_dir = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Slate"
    return Path(app_dir) / JOURNAL_FOLDER


def _common_folder(paths: List[Path]) -> Optional[Path]:
    try:
        return Path(os.path.commonpath([str(p.parent) for p in paths]))
    except ValueError:
        return None


def write_journal(renamed: List[Tuple[Path, Path]], user: str = "", app_dir=None) -> Optional[Path]:
    """
    Record a finished rename so it can be undone. Nothing renamed, no journal.

    Paths are stored relative to the folder the files share, so the journal
    still describes them after the folder is reached through another drive
    letter; the absolute paths are kept as well for the message.
    """
    if not renamed:
        return None
    folder = _common_folder([old for old, _ in renamed])
    pairs = []
    for old, new in renamed:
        entry = {"old": str(old), "new": str(new)}
        if folder is not None:
            entry["old_rel"] = os.path.relpath(old, folder)
            entry["new_rel"] = os.path.relpath(new, folder)
        pairs.append(entry)
    stamp = datetime.now()
    record = {
        "id": f"{stamp:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}",
        "folder": str(folder) if folder else "",
        "user": user,
        "time": stamp.isoformat(timespec="seconds"),
        "count": len(pairs),
        "pairs": pairs,
        "undone": False,
    }
    directory = journal_dir(app_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{record['id']}.json"
        path.write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
        return path
    except OSError as exc:
        logger.warning("Could not write the rename undo journal: %s", exc)
        return None


def latest_journal(app_dir=None) -> Optional[Path]:
    """The newest rename that has not been undone yet."""
    directory = journal_dir(app_dir)
    try:
        candidates = sorted(directory.glob("*.json"), reverse=True)
    except OSError:
        return None
    for path in candidates:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not record.get("undone"):
            return path
    return None


def read_journal(path) -> Dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _journal_pairs(record: Dict) -> List[Tuple[Path, Path]]:
    folder = record.get("folder") or ""
    pairs = []
    for entry in record.get("pairs") or []:
        if folder and entry.get("old_rel") and entry.get("new_rel"):
            pairs.append((Path(folder) / entry["old_rel"], Path(folder) / entry["new_rel"]))
        else:
            pairs.append((Path(entry["old"]), Path(entry["new"])))
    return pairs


@dataclass
class UndoResult:
    restored: int = 0
    refused: str = ""
    failed: List[Tuple[Path, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refused and not self.failed


def undo(journal_path) -> UndoResult:
    """
    Put the names back. Refused - with nothing touched - when the files have
    changed since: a renamed file gone, or its old name taken by a file that
    is not part of this rename.
    """
    result = UndoResult()
    try:
        record = read_journal(journal_path)
    except (OSError, ValueError) as exc:
        result.refused = f"The undo record could not be read: {exc}"
        return result
    if record.get("undone"):
        result.refused = "This rename has already been undone."
        return result

    pairs = _journal_pairs(record)
    currently = {str(new).lower() for _, new in pairs}
    missing = [new.name for _, new in pairs if not new.exists()]
    taken = [old.name for old, _ in pairs if old.exists() and str(old).lower() not in currently]
    if missing:
        shown = ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else "")
        result.refused = (f"{len(missing)} renamed file(s) are no longer there ({shown}). "
                          "They were moved or renamed again, so nothing was changed.")
        return result
    if taken:
        shown = ", ".join(taken[:5]) + (" ..." if len(taken) > 5 else "")
        result.refused = (f"{len(taken)} old name(s) are now used by other files ({shown}), "
                          "so nothing was changed.")
        return result

    outcome = rename_files([(new, old) for old, new in pairs])
    result.restored = outcome.count
    result.failed = outcome.failed
    record["undone"] = True
    record["undone_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        Path(journal_path).write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not mark the undo journal as used: %s", exc)
    return result
