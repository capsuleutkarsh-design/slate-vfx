"""
The shot folder standard.

One rule holds the whole thing together: **Output means what leaves this
department**, and nothing else uses that word. Client delivery is 08_Deliver.
These tests fail if that ever drifts again.
"""

import io
import json
from collections import defaultdict

import pytest

from ut_vfx.core.domain.departments import load_departments


@pytest.fixture(scope="module")
def shot_folders():
    with io.open("ut_vfx/data/templates.json", encoding="utf-8") as handle:
        templates = json.load(handle)
    standard = templates["standard"].get("structure", templates["standard"])
    return standard["shot_folders"]


@pytest.fixture(scope="module")
def by_top(shot_folders):
    grouped = defaultdict(set)
    for entry in shot_folders:
        head, _, tail = entry.partition("/")
        grouped[head]   # ensure the key exists even with no subfolders
        if tail:
            grouped[head].add(tail)
    return grouped


# Folders that are not departmental work areas.
NON_DEPARTMENT = {"00_Annotation", "01_Scan", "08_Deliver"}


class TestOneWordOneMeaning:
    """'Output' must mean exactly one thing."""

    def test_no_department_uses_render_or_final(self, shot_folders):
        offenders = [f for f in shot_folders
                     if f.split("/")[-1].lower() in {"render", "renders", "final"}]
        assert not offenders, (
            f"these should be Output: {offenders}"
        )

    def test_client_delivery_is_not_called_output(self, by_top):
        assert "08_Deliver" in by_top, "client delivery folder is missing"
        assert "08_Output" not in by_top, (
            "08_Output is back - 'Output' now means two different things again"
        )

    def test_client_delivery_has_no_script(self, by_top):
        """Delivery is not a department; nobody works in it."""
        subs = {s.lower() for s in by_top["08_Deliver"]}
        assert "script" not in subs


class TestEveryDepartmentLooksTheSame:

    def test_each_department_has_script_and_output(self, by_top):
        missing = []
        for head, subs in by_top.items():
            if head in NON_DEPARTMENT:
                continue
            lowered = {s.split("/")[0].lower() for s in subs}
            if "script" not in lowered:
                missing.append(f"{head} has no Script")
            if "output" not in lowered:
                missing.append(f"{head} has no Output")
        assert not missing, missing

    def test_every_tracked_department_has_a_folder(self, by_top, shot_folders):
        tops = set(by_top)
        for dept in load_departments():
            assert dept.folder, f"{dept.key} has no folder"
            head = dept.folder.split("/")[0]
            assert head in tops, f"{dept.key} points at {dept.folder}, which is not in the template"

    def test_slapcomp_sits_with_comp_and_has_its_own_script(self, shot_folders):
        from ut_vfx.core.domain.departments import get_department

        assert get_department("slapcomp").folder == "07_Comp/Slapcomp"
        assert "07_Comp/Slapcomp/Script" in shot_folders
        assert "07_Comp/Slapcomp/Output" in shot_folders

    def test_matchmove_can_deliver_cameras_and_geo(self, shot_folders):
        assert "06_Cmm/Output/Cam" in shot_folders
        assert "06_Cmm/Output/Geo" in shot_folders


class TestShape:

    def test_nothing_is_deeper_than_three_levels(self, shot_folders):
        too_deep = [f for f in shot_folders if len(f.split("/")) > 3]
        assert not too_deep, f"too deep to navigate comfortably: {too_deep}"

    def test_scan_format_folders_are_not_created_statically(self, shot_folders):
        """
        Scans live in 01_Scan/vNNN/<FORMAT>, created per delivery. A static
        01_Scan/EXR would sit empty forever and confuse everyone.
        """
        stale = [f for f in shot_folders
                 if f.startswith("01_Scan/") and f != "01_Scan"]
        assert not stale, f"these would never be filled: {stale}"

    def test_no_duplicate_entries(self, shot_folders):
        assert len(shot_folders) == len(set(shot_folders))


class TestScanVersionFolders:

    def test_denoise_is_configured_per_scan_version(self):
        with io.open("ut_vfx/data/templates.json", encoding="utf-8") as handle:
            templates = json.load(handle)
        standard = templates["standard"].get("structure", templates["standard"])
        assert "Denoise" in standard.get("scan_version_folders", [])

    def test_each_delivery_gets_its_own_denoise_folder(self, temp_vfx_root, mock_db):
        """
        The degrained plate belongs to the scan it came from, so a re-delivery
        gets its own Denoise rather than sharing one.
        """
        from ut_vfx.core.workers.structure import FolderCreationWorker
        import ut_vfx.core.workers.structure as structure_module

        structure_module.database_manager = mock_db

        with io.open("ut_vfx/data/templates.json", encoding="utf-8") as handle:
            standard = json.load(handle)["standard"]
        st = standard.get("structure", standard)

        source = temp_vfx_root / "Drive"
        for folder in ("SH010", "SH010_ScanB"):
            d = source / "ReelA" / folder
            d.mkdir(parents=True)
            (d / "SH010.0001.exr").write_bytes(b"x")

        target = temp_vfx_root / "Projects"
        target.mkdir()

        worker = FolderCreationWorker(
            target_dir=target, source_scan_path=source, project_name="PRJ",
            template_data=(st["base_folders"], [], [], st["shot_folders"]),
            fast_mode=True, format_mapping={},
            scan_version_folders=st.get("scan_version_folders"),
        )
        worker.run()

        scan = target / "PRJ" / "05_Reels" / "ReelA" / "SH010" / "01_Scan"
        assert (scan / "v001" / "Denoise").is_dir()
        assert (scan / "v002" / "Denoise").is_dir()
        # The plates themselves are separated too.
        assert (scan / "v001" / "EXR" / "SH010.0001.exr").exists()
        assert (scan / "v002" / "EXR" / "SH010.0001.exr").exists()
