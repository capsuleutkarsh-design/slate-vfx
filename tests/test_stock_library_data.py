"""
The stock library's data, on SQLite and on a real PostgreSQL.

    MED-001  analysis results are stored by path, not lost
    MED-003  tags are never split into letters; import/export round-trips
    MED-004  sort orders, stable across pages
    MED-005  visual filter matches whole tags
    MED-006  filtered totals through every facade
    MED-007  category filtered in the database, with paging and count
    MED-009  favourites per person; studio picks
    MED-011  sequence name and range survive a reload
    MED-012  categories with counts from the stored column
    MED-015  Clear Library removes the cached files
    MED-025  % and _ are literal in a search
    MED-026  resolution, codec and category are searchable
    MED-034  delete is undoable, then purged with its cache files
"""

import json
from datetime import datetime, timedelta

import pytest

from slate.core.domain.stock_search import (
    build_search_text, escape_like, normalise_tags, tags_text, visual_text,
)


@pytest.fixture(params=["sqlite", "postgres"])
def db(request):
    if request.param == "sqlite":
        yield request.getfixturevalue("mock_db")
    else:
        yield request.getfixturevalue("pg_db")


@pytest.fixture
def lib(db):
    from slate.core.domain.library_manager import LibraryManager
    return LibraryManager(db, username="priya")


def _asset(path, **extra):
    record = {"file_path": str(path), "tags": [], "metadata": {}}
    record.update(extra)
    return record


def _seed(lib, tmp_path, names, **extra):
    assets = []
    for name in names:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 10)
        assets.append(_asset(p, **extra))
    lib.add_assets_batch(assets)
    return assets


# ------------------------------------------------------------------ helpers

class TestTags:
    def test_the_shapes_tags_arrive_in(self):
        assert normalise_tags("Fire, Smoke") == ["Fire", "Smoke"]
        assert normalise_tags(["Fire", " smoke ", "fire"]) == ["Fire", "smoke"]
        assert normalise_tags("['Fire', 'Smoke']") == ["Fire", "Smoke"]
        assert normalise_tags(None) == []
        assert normalise_tags("") == []

    def test_letters_are_mended_into_the_word(self):
        assert normalise_tags("P,e,n,d,i,n,g") == ["Pending"]

    def test_placeholders_are_never_stored(self):
        assert tags_text(["Pending"]) == ""
        assert tags_text("Fire,Pending") == "Fire"

    def test_visual_tags_only_known_ones_in_order(self):
        assert visual_text(["warm", "nonsense", "Dark"]) == "Dark,Warm"

    def test_like_wildcards_are_escaped(self):
        assert escape_like("a_b%c\\") == "a\\_b\\%c\\\\"

    def test_search_text_holds_resolution_words(self):
        text = build_search_text({"file_path": "C:/stock/Smoke/plume.mov", "category": "Smoke",
                                  "metadata": {"width": 3840, "height": 2160, "fps": 23.976,
                                               "codec": "prores", "duration_sec": 5}})
        for word in ("3840x2160", "4k", "uhd", "23.976fps", "prores", "smoke", "clip"):
            assert word in text.split()


# ------------------------------------------------------------------ writing

def test_analysis_is_stored_by_path(lib, db, tmp_path):
    (a,) = _seed(lib, tmp_path, ["fire.jpg"])
    changed = lib.update_assets_batch([{"file_path": a["file_path"], "tags": ["Fire", "Hot"],
                                        "metadata": {"width": 320, "height": 180},
                                        "thumb_path": "C:/cache/t.jpg"}])
    assert changed == 1
    row = dict(db.execute_query("SELECT tags, metadata, thumb_path FROM stock_library",
                                fetch="one"))
    assert row["tags"] == "Fire,Hot"
    assert json.loads(row["metadata"])["width"] == 320
    assert row["thumb_path"] == "C:/cache/t.jpg"


def test_reingesting_keeps_what_was_learned(lib, db, tmp_path):
    (a,) = _seed(lib, tmp_path, ["smoke.png"])
    lib.update_assets_batch([{"file_path": a["file_path"], "tags": ["Smoke"],
                              "metadata": {"width": 10, "height": 10}}])
    # The ingest stores a new file as "being analysed": no tags, no metadata.
    lib.add_assets_batch([_asset(a["file_path"], tags=["Pending"])])
    row = dict(db.execute_query("SELECT tags, metadata FROM stock_library", fetch="one"))
    assert row["tags"] == "Smoke"
    assert json.loads(row["metadata"]) == {"width": 10, "height": 10}


def test_export_and_import_round_trip_keeps_tags(lib, db, tmp_path):
    from slate.core.domain.asset_ingestor import ImportLibWorker
    (a,) = _seed(lib, tmp_path, ["fire.exr"])
    lib.set_tags(lib.search_library()[0], ["Fire", "Smoke"])
    exported = json.loads(json.dumps(lib.search_library(), default=str))
    entries, error = ImportLibWorker.validate(exported)
    assert not error
    lib.add_assets_batch(entries)
    row = dict(db.execute_query("SELECT tags FROM stock_library", fetch="one"))
    assert row["tags"] == "Fire,Smoke"


def test_import_refuses_what_is_not_an_export():
    from slate.core.domain.asset_ingestor import ImportLibWorker
    entries, error = ImportLibWorker.validate({"name": "not a list"})
    assert entries == [] and "not a Slate library export" in error
    entries, error = ImportLibWorker.validate([{"x": 1}])
    assert "not a Slate library export" in error


def test_a_sequence_comes_back_as_a_sequence(lib, tmp_path):
    first = tmp_path / "muzzle.1001.png"
    first.write_bytes(b"x")
    lib.add_assets_batch([_asset(first, display_name="muzzle.[1001-1024].png", is_sequence=True,
                                 frame_first=1001, frame_last=1024, frame_count=24,
                                 pattern=str(tmp_path / "muzzle.%04d.png"))])
    (asset,) = lib.search_library()
    assert asset["name"] == "muzzle.[1001-1024].png"
    assert asset["is_sequence"] and asset["frame_count"] == 24
    assert asset["file_name"] == "muzzle.1001.png"


# ------------------------------------------------------------------ reading

def test_sort_orders_hold_across_pages(lib, db, tmp_path):
    _seed(lib, tmp_path, ["b.jpg", "a.jpg", "c.jpg"])
    ids = {dict(r)["file_name"]: dict(r)["id"] for r in db.execute_query(
        "SELECT id, file_name FROM stock_library")}
    for offset, name in enumerate(["a.jpg", "b.jpg", "c.jpg"]):
        db.execute_update("UPDATE stock_library SET ingest_date = %s WHERE id = %s",
                          (datetime(2026, 1, 1 + offset), ids[name]))
    def names(sort):
        page1 = lib.search_library(limit=2, offset=0, sort=sort)
        page2 = lib.search_library(limit=2, offset=2, sort=sort)
        return [a["file_name"] for a in page1 + page2]
    assert names("newest") == ["c.jpg", "b.jpg", "a.jpg"]
    assert names("oldest") == ["a.jpg", "b.jpg", "c.jpg"]
    assert names("name") == ["a.jpg", "b.jpg", "c.jpg"]


def test_search_takes_percent_and_underscore_literally(lib, tmp_path):
    _seed(lib, tmp_path, ["lib/stock/a_b.jpg", "lib/stock/axb.jpg", "lib/stock/100%.png"])
    assert [a["file_name"] for a in lib.search_library(query="_")] == ["a_b.jpg"]
    assert [a["file_name"] for a in lib.search_library(query="%")] == ["100%.png"]


def test_search_reaches_resolution_and_category(lib, tmp_path):
    (a,) = _seed(lib, tmp_path, ["plume.mov"], category="Stock Elements")
    lib.update_assets_batch([{"file_path": a["file_path"], "category": "Stock Elements",
                              "metadata": {"width": 3840, "height": 2160, "duration_sec": 4}}])
    assert len(lib.search_library(query="4K")) == 1
    assert len(lib.search_library(query="3840")) == 1
    assert len(lib.search_library(query="stock elements")) == 1
    assert lib.search_library(query="8K") == []


def test_category_is_filtered_with_the_paging_and_the_count(lib, tmp_path):
    _seed(lib, tmp_path, [f"fire_{i:03d}.mov" for i in range(7)], category="Fire")
    _seed(lib, tmp_path, ["fog.mov", "mist.mov"], category="Atmosphere")
    page = lib.search_library(limit=5, offset=0, category="Fire")
    rest = lib.search_library(limit=5, offset=5, category="Fire")
    assert len(page) == 5 and len(rest) == 2
    assert lib.get_total_count(category="Fire") == 7
    assert lib.get_category_counts() == {"Fire": 7, "Atmosphere": 2}


def test_the_count_goes_through_every_facade(lib, tmp_path):
    from slate.core.domain.asset_api import AssetAPI
    from slate.core.domain.openassetio_backend import OpenAssetIOBackend
    _seed(lib, tmp_path, ["one.jpg", "two.jpg"])
    api = AssetAPI(OpenAssetIOBackend(lib))
    assert api.get_total_count(query="one") == 1
    assert api.get_total_count() == 2


def test_visual_filter_matches_whole_tags(lib, tmp_path):
    (warm,) = _seed(lib, tmp_path, ["sunset.jpg"])
    (other,) = _seed(lib, tmp_path, ["other.jpg"])
    lib.update_assets_batch([{"file_path": warm["file_path"], "visual_tags": ["Warm", "Bright"]}])
    lib.repo.db.execute_update("UPDATE stock_library SET visual_tags = 'Warmup' WHERE file_path = %s",
                               (other["file_path"],))
    assert [a["file_name"] for a in lib.search_library(visual="Warm")] == ["sunset.jpg"]
    assert lib.get_total_count(visual="Warm") == 1


# --------------------------------------------------------------- favourites

def test_favourites_belong_to_one_person(lib, db, tmp_path):
    from slate.core.domain.library_manager import LibraryManager
    _seed(lib, tmp_path, ["a.jpg", "b.jpg"])
    first = lib.search_library(sort="name")[0]
    assert lib.set_favorite(first["id"], True)
    mine = lib.search_library(category="Favorites")
    assert [a["file_name"] for a in mine] == ["a.jpg"] and mine[0]["is_favorite"]
    other = LibraryManager(db, username="rahul")
    assert other.search_library(category="Favorites") == []
    assert not other.search_library(sort="name")[0]["is_favorite"]
    lib.set_favorite(first["id"], False)
    assert lib.search_library(category="Favorites") == []


def test_studio_picks_are_shared(lib, db, tmp_path):
    from slate.core.domain.library_manager import LibraryManager
    _seed(lib, tmp_path, ["a.jpg", "b.jpg"])
    target = lib.search_library(sort="name")[1]
    assert lib.set_pick(target["id"], True)
    other = LibraryManager(db, username="rahul")
    picks = other.search_library(category="Studio picks")
    assert [a["file_name"] for a in picks] == ["b.jpg"] and picks[0]["is_pick"]
    assert other.get_pick_count() == 1


# ----------------------------------------------------------------- deleting

def test_delete_can_be_undone(lib, tmp_path):
    _seed(lib, tmp_path, ["a.jpg", "b.jpg"])
    a = lib.search_library(sort="name")[0]
    gone = lib.delete_assets([a])
    assert gone == [int(a["id"])]
    assert [x["file_name"] for x in lib.search_library()] == ["b.jpg"]
    assert lib.get_total_count() == 1
    # Still known to the ingest, as deleted, so a Rescan leaves it out (MED2-002).
    known = {p["file_path"]: p for p in lib.list_known_paths()}
    assert known[a["file_path"]]["deleted_at"]
    assert lib.restore_assets(gone) == 1
    assert lib.get_total_count() == 2


def test_purge_removes_the_cache_and_keeps_the_deletion(lib, db, tmp_path):
    thumb = tmp_path / "cache" / "a_thumb.jpg"
    thumb.parent.mkdir()
    thumb.write_bytes(b"jpg")
    (a,) = _seed(lib, tmp_path, ["a.jpg"], thumb_path=str(thumb))
    asset = lib.search_library()[0]
    lib.set_favorite(asset["id"], True)
    lib.delete_assets([asset])
    # Not yet old enough.
    assert lib.purge_deleted() == 0
    assert thumb.exists()
    assert lib.purge_deleted(older_than=datetime.now() + timedelta(minutes=1)) == 1
    assert not thumb.exists()
    # The row stays, deleted, so a Rescan does not add the file again (MED2-002),
    # and Removed can still restore it (MED2-028).
    rows = [dict(r) for r in db.execute_query("SELECT thumb_path, deleted_at FROM stock_library")]
    assert len(rows) == 1 and rows[0]["deleted_at"] and not rows[0]["thumb_path"]
    assert lib.purge_deleted(older_than=datetime.now() + timedelta(minutes=1)) == 0
    assert lib.list_favorites() == set()
    assert lib.get_removed_count() == 1
    assert [x["file_name"] for x in lib.search_library(category="Removed")] == ["a.jpg"]


def test_reingesting_a_deleted_file_brings_it_back(lib, tmp_path):
    (a,) = _seed(lib, tmp_path, ["a.jpg"])
    lib.delete_assets(lib.search_library())
    lib.add_assets_batch([a])
    assert lib.get_total_count() == 1


def test_clear_library_removes_cached_files(lib, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    thumbs = []
    for i in range(3):
        t = cache / f"{i}_thumb.jpg"
        t.write_bytes(b"jpg")
        thumbs.append(t)
        _seed(lib, tmp_path, [f"src{i}.jpg"], thumb_path=str(t))
    keep = cache / "unrelated.txt"
    keep.write_text("not ours")
    ok, removed, failed = lib.clear_all_assets()
    assert ok and removed == 3 and failed == 0
    assert not any(t.exists() for t in thumbs)
    assert keep.exists()
    assert lib.get_total_count() == 0


# ----------------------------------------------------------------- repairs

def test_old_damaged_rows_are_repaired(db):
    from slate.core.infra.migrations.media_schema import repair_stock_library
    db.execute_update(
        "INSERT INTO stock_library (file_path, file_name, file_type, tags, metadata) "
        "VALUES (%s, %s, %s, %s, %s)",
        ("C:/stock/Fire/flame_burst.mov", "flame_burst.mov", ".mov", "P,e,n,d,i,n,g", "{}"))
    assert repair_stock_library(db) is not False
    row = dict(db.execute_query(
        "SELECT tags, category, display_name, search_text FROM stock_library", fetch="one"))
    assert row["category"] == "Fire"
    assert "Pending" not in row["tags"] and "P,e" not in row["tags"]
    # The words of the name; the category is not a tag (MED2-037).
    assert row["tags"].split(",") == ["flame", "burst"]
    assert row["display_name"] == "flame_burst.mov"
    assert "fire" in row["search_text"].split()


# ------------------------------------------------- words (MED2-005/014/026/037)

def test_search_matches_the_start_of_words(lib, tmp_path):
    _seed(lib, tmp_path, ["uhd_4k_clip.mp4", "hd_plate.mov", "FireballLoop.mov"])
    lib.update_assets_batch([{"file_path": str(tmp_path / "hd_plate.mov"),
                              "metadata": {"width": 1920, "height": 1080, "duration_sec": 2,
                                           "fps": 23.976}}])
    names = lambda q: sorted(a["file_name"] for a in lib.search_library(query=q))
    assert names("HD") == ["hd_plate.mov"]                 # not inside "uhd"
    assert names("movie") == ["hd_plate.mov"]              # the Type column's word
    assert names("loop") == ["FireballLoop.mov"]
    assert names("23.976fps") == ["hd_plate.mov"]
    assert names("4k_clip") == ["uhd_4k_clip.mp4"]


def test_tags_are_the_telling_words():
    from pathlib import Path
    from slate.core.domain.metadata_engine import SmartMetadataManager as M
    category, tags = M.get_smart_tags(Path(
        "S/long_descriptive_element_name_for_tests_extremely_with_file.mov"))
    assert not {"with", "for", "name", "file", "tests"} & set(tags)
    assert M.get_smart_tags(Path("S/long_reference_65min.mp4"))[1] == ["long", "reference"]
    category, tags = M.get_smart_tags(Path("S/smoke_plume.mov"))
    assert category == "Smoke" and "smoke" not in tags and tags == ["plume"]


def test_old_rows_get_todays_words(db):
    from slate.core.infra.migrations.media_schema import reword_stock_library
    db.execute_update(
        "INSERT INTO stock_library (file_path, file_name, file_type, tags, metadata, category, "
        "search_text) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        ("C:/stock/Plates/film_grain_35mm.exr", "film_grain_35mm.exr", ".exr",
         "HDRI,film,grain,with,MyOwnTag", "{}", "HDRI", "film grain"))
    assert reword_stock_library(db) is not False
    row = dict(db.execute_query("SELECT tags, category, search_text FROM stock_library",
                                fetch="one"))
    assert row["category"] == "References"          # "plates", not HDRI for an .exr
    assert row["tags"] == "film,grain,MyOwnTag"
    assert "exr" in row["search_text"].split() and "image" not in row["search_text"].split()
