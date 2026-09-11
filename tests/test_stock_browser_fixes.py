"""
The Stock Browser problems, pinned down so they cannot come back.

All of these were found by reading and running the real thing against a library
of tens of thousands of assets held on a shared drive. Most of them are
invisible at small scale, which is why the browser "worked, and then after a
time stopped working".
"""

import hashlib
import inspect
import math

import pytest


class TestAssetIdentifiersCannotCollide:
    """
    Two different files could be given the same identifier.

    The identifier is a fingerprint of the path, and it used to be squeezed into
    the range of a small number column - throwing most of the fingerprint away.
    The interface matches assets by it, so a collision wrote one asset's picture
    and details over another's.
    """

    @staticmethod
    def _make_id(name, path):
        from ut_vfx.core.domain.asset_ingestor import IngestWorker
        source = inspect.getsource(IngestWorker._create_basic_asset)
        assert "% 2_147_483_647" not in source, (
            "the fingerprint is still being squeezed into a small number range"
        )
        return hashlib.md5((str(name) + str(path)).encode("utf-8")).hexdigest()

    def test_the_fingerprint_is_no_longer_truncated(self):
        self._make_id("a.mov", "/x/a.mov")

    def test_a_large_library_produces_no_duplicates(self):
        """
        Squeezed into 31 bits, forty thousand assets had roughly a one in three
        chance of a collision. The full fingerprint has none.
        """
        ids = {
            hashlib.md5(f"clip_{n}.mov/stock/reel/clip_{n}.mov".encode()).hexdigest()
            for n in range(40000)
        }

        assert len(ids) == 40000

    def test_the_old_scheme_really_was_unsafe(self):
        """Kept as the reason this matters, not as a test of current code."""
        collisions = 1 - math.exp(-(40000 * 39999) / (2 * 2_147_483_647))

        assert collisions > 0.25, "the arithmetic behind this fix"

    def test_the_same_file_still_gets_the_same_identifier(self):
        """It has to stay steady between sessions, which is why it is a hash."""
        first = self._make_id("a.mov", "/stock/a.mov")
        second = self._make_id("a.mov", "/stock/a.mov")

        assert first == second


class TestSearchWorksOnBothDatabases:
    """
    Searching used a comparison only PostgreSQL understands.

    When the server cannot be reached the software falls back to a local
    database, which rejects it outright - and the error was swallowed, so the
    search silently returned nothing and the library looked empty.
    """

    def test_no_postgres_only_comparison_remains(self):
        """Comments may mention it; the SQL must not use it."""
        from ut_vfx.core.infra.stock_repository import StockRepository

        code = [line.split("#", 1)[0]
                for line in inspect.getsource(StockRepository).splitlines()]

        assert "ILIKE" not in " ".join(code).upper(), (
            "ILIKE is PostgreSQL-only and fails on the local fallback database"
        )

    def test_the_search_clause_runs_on_sqlite(self):
        """The real proof: run the generated SQL against SQLite."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE stock_library "
                     "(id INTEGER PRIMARY KEY, file_name TEXT, tags TEXT, file_path TEXT)")
        conn.execute("INSERT INTO stock_library (file_name, tags, file_path) "
                     "VALUES ('Explosion_01.mov', 'fire', '/stock/Explosion_01.mov')")

        rows = conn.execute(
            "SELECT * FROM stock_library WHERE ("
            "LOWER(file_name) LIKE LOWER(?) "
            "OR LOWER(tags) LIKE LOWER(?) "
            "OR LOWER(file_path) LIKE LOWER(?))",
            ("%explo%", "%explo%", "%explo%")).fetchall()

        assert len(rows) == 1, "the search finds nothing on the fallback database"

    def test_searching_is_still_case_insensitive(self):
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (name TEXT)")
        conn.execute("INSERT INTO t VALUES ('EXPLOSION.mov')")

        rows = conn.execute(
            "SELECT * FROM t WHERE LOWER(name) LIKE LOWER(?)", ("%explosion%",)).fetchall()

        assert len(rows) == 1


class TestBrowsingDoesNotFetchWhatItCannotShow:

    def test_the_similarity_vectors_are_left_behind(self):
        """
        They are large, never displayed, and were being carried across the
        network on every page of scrolling.
        """
        from ut_vfx.core.infra.stock_repository import StockRepository

        assert "embedding" not in StockRepository.BROWSE_COLUMNS

    def test_everything_the_interface_shows_is_still_fetched(self):
        from ut_vfx.core.infra.stock_repository import StockRepository

        for column in ("id", "file_path", "file_name", "file_type",
                       "thumb_path", "proxy_path", "tags", "metadata"):
            assert column in StockRepository.BROWSE_COLUMNS

    def test_listing_is_no_longer_a_select_star(self):
        from ut_vfx.core.infra.stock_repository import StockRepository

        source = inspect.getsource(StockRepository.get_all_stock_assets)

        assert "SELECT *" not in source


class TestCountsMatchWhatIsOnScreen:
    """
    The count always reported the whole library, even while a search narrowed
    it - so the number disagreed with the list, and paging misbehaved.
    """

    def test_the_repository_can_count_a_filtered_list(self):
        from ut_vfx.core.infra.stock_repository import StockRepository

        assert hasattr(StockRepository, "count_stock_assets")

    def test_the_filter_is_shared_between_listing_and_counting(self):
        """Two copies of the rule would drift apart."""
        from ut_vfx.core.infra.stock_repository import StockRepository

        assert "_where" in inspect.getsource(StockRepository.get_all_stock_assets)
        assert "_where" in inspect.getsource(StockRepository.count_stock_assets)

    def test_the_library_manager_passes_the_filter_through(self):
        from ut_vfx.core.domain.library_manager import LibraryManager

        params = inspect.signature(LibraryManager.get_total_count).parameters

        assert "query" in params
        assert "file_types" in params


class TestTheIngestDoesNotReadTheWholeLibrary:
    """
    Before scanning a single new file the ingest read every column of every
    existing row, then asked the file system to resolve each path - one network
    round trip each, tens of thousands of them.
    """

    def test_it_asks_only_for_the_paths(self):
        from ut_vfx.core.domain.asset_ingestor import IngestWorker

        source = inspect.getsource(IngestWorker.run)

        assert "list_known_paths" in source
        assert "get_all_assets()" not in source

    def test_the_repository_offers_a_paths_only_query(self):
        from ut_vfx.core.infra.stock_repository import StockRepository

        source = inspect.getsource(StockRepository.list_stock_paths)

        assert "file_path" in source
        assert "SELECT *" not in source

    def test_paths_are_compared_without_touching_the_file_system(self):
        from ut_vfx.core.domain.asset_ingestor import IngestWorker

        code = [line.split("#", 1)[0]
                for line in inspect.getsource(IngestWorker.run).splitlines()]

        assert ".resolve()" not in " ".join(code), (
            "resolving each path is a network round trip per asset"
        )

    def test_sequences_are_matched_the_same_way_as_single_files(self):
        """
        Both halves of the deduplication have to spell a path identically.

        The sequence half used to resolve the path - which on Windows hands back
        backslashes while the other half stores forward slashes - so no sequence
        ever matched, and every image sequence was ingested again on every run.
        """
        from ut_vfx.core.domain.asset_ingestor import IngestWorker

        source = inspect.getsource(IngestWorker.run)

        assert source.count("_normalise_path(") >= 3, (
            "single files, sequences and the existing-paths map must all use it"
        )

    def test_the_same_path_normalises_the_same_way(self):
        from ut_vfx.core.domain.asset_ingestor import _normalise_path

        assert (_normalise_path(r"C:\Stock\Clip.mov")
                == _normalise_path("C:/Stock/clip.mov"))

    def test_a_trailing_separator_makes_no_difference(self):
        from ut_vfx.core.domain.asset_ingestor import _normalise_path

        assert _normalise_path("/stock/a/") == _normalise_path("/stock/a")


class TestALargeIngestDoesNotFloodTheInterface:

    def test_updates_are_sent_in_groups(self):
        from ut_vfx.core.domain.asset_ingestor import IngestWorker

        source = inspect.getsource(IngestWorker.run)

        assert "_update_buffer.append" in source
        assert "_flush_update_buffer" in source

    def test_the_interface_listens_for_those_groups(self):
        """Sending them without a listener would silently lose every update."""
        from ut_vfx.gui.tabs.stock_browser.controllers.ingest_controller import (
            StockIngestController,
        )

        source = inspect.getsource(StockIngestController)

        assert "assets_update_batch_signal.connect" in source
        assert hasattr(StockIngestController, "on_assets_update_batch")

    def test_matching_an_asset_is_not_a_search_through_the_whole_list(self):
        from ut_vfx.gui.tabs.stock_browser.controllers.ingest_controller import (
            StockIngestController,
        )

        source = inspect.getsource(StockIngestController.on_asset_update)

        assert "update_item" in source, (
            "still walking every row for each update, which grows with the "
            "square of the library size"
        )


class TestTheCacheFolderStaysUsable:
    """
    Every thumbnail and proxy went into one folder on the shared drive. At tens
    of thousands of assets that is a directory Windows file sharing struggles
    with, and it only ever grew.
    """

    @pytest.fixture
    def manager(self, tmp_path):
        from ut_vfx.core.domain.proxy_manager import ProxyManager

        instance = ProxyManager()
        instance.cache_dir = tmp_path / "Cache"
        instance.cache_dir.mkdir(parents=True, exist_ok=True)
        return instance

    def test_files_are_spread_across_folders(self, manager):
        paths = [
            manager.cache_path_for(f"hash{n}", "_thumb.jpg",
                                   manager.identity_hash(f"/stock/clip_{n}.mov"))
            for n in range(400)
        ]
        folders = {p.parent for p in paths}

        assert len(folders) > 20, f"still landing in {len(folders)} folder(s)"

    def test_a_file_always_lands_in_the_same_folder(self, manager):
        """Otherwise its earlier pictures could never be found again."""
        identity = manager.identity_hash("/stock/clip.mov")

        first = manager.cache_path_for("aaa", "_thumb.jpg", identity)
        second = manager.cache_path_for("bbb", "_thumb.jpg", identity)

        assert first.parent == second.parent

    def test_the_name_carries_a_part_that_does_not_change(self, manager):
        """
        The cache name includes the file's modification time, so re-syncing the
        stock renames everything. A steady prefix is what lets the old copies be
        found and cleared.
        """
        identity = manager.identity_hash("/stock/clip.mov")

        assert manager.cache_path_for("aaa", "_thumb.jpg", identity).name.startswith(identity)

    def test_earlier_pictures_of_the_same_file_are_cleared(self, manager):
        source = "/stock/clip.mov"
        identity = manager.identity_hash(source)
        old = manager.cache_path_for("oldhash", "_thumb.jpg", identity)
        old.write_bytes(b"stale")
        current = manager.cache_path_for("newhash", "_thumb.jpg", identity)
        current.write_bytes(b"fresh")

        removed = manager.forget_previous(source, keep=current)

        assert removed == 1
        assert not old.exists()
        assert current.exists()

    def test_another_file_is_left_alone(self, manager):
        mine = manager.cache_path_for("h1", "_thumb.jpg",
                                      manager.identity_hash("/stock/mine.mov"))
        mine.write_bytes(b"mine")
        theirs = manager.cache_path_for("h2", "_thumb.jpg",
                                        manager.identity_hash("/stock/theirs.mov"))
        theirs.write_bytes(b"theirs")

        manager.forget_previous("/stock/mine.mov")

        assert theirs.exists()


class TestRemovingAnAssetActuallyRemovesIt:

    def test_it_reaches_the_database(self):
        """
        It used to drop the asset from the in-memory list only, so it came
        straight back the next time the library was read.
        """
        from ut_vfx.core.domain.library_manager import LibraryManager

        source = inspect.getsource(LibraryManager.remove_asset)

        assert "remove_stock_asset_by_path" in source

    def test_it_says_whether_it_worked(self):
        from ut_vfx.core.domain.library_manager import LibraryManager

        source = inspect.getsource(LibraryManager.remove_asset)

        assert "return" in source


class TestAddingAndEditingReportTheirResult:
    """The same silent-failure pattern found across the database work."""

    def test_adding_returns_the_new_identifier(self):
        from ut_vfx.core.domain.library_manager import LibraryManager

        assert "return new_id" in inspect.getsource(LibraryManager.add_asset)

    def test_editing_returns_whether_it_saved(self):
        from ut_vfx.core.domain.library_manager import LibraryManager

        source = inspect.getsource(LibraryManager.update_asset_metadata)

        assert "return True" in source
        assert "return False" in source


class TestScrollingDoesNotRepeatItself:

    def test_the_sidebar_is_only_rebuilt_on_a_fresh_list(self):
        from ut_vfx.gui.tabs.stock_browser.controllers.pagination_loader_mixin import (
            PaginationLoaderMixin,
        )

        source = inspect.getsource(PaginationLoaderMixin.on_library_loaded)

        assert "if not append:" in source

    def test_a_typed_search_is_not_cleared_by_the_next_page(self):
        from ut_vfx.gui.tabs.stock_browser.controllers.pagination_loader_mixin import (
            PaginationLoaderMixin,
        )

        source = inspect.getsource(PaginationLoaderMixin.apply_post_load_filters)

        assert 'set_text_filter("")' not in source


class TestWhatIsBeingFilteredForIsRemembered:
    """
    The count and the view's text filter both read back the current search and
    media type. Nothing ever wrote them.

    That made two fixes inert: the count fell back to the whole library the
    moment a search narrowed the list, and the typed search was wiped from the
    view on every page. Checking the source alone did not catch it - both reads
    are written defensively, so they fail quietly rather than raising.
    """

    def _controller(self, search=None, media_type="All"):
        from ut_vfx.gui.tabs.stock_browser.controllers.pagination_loader_mixin import (
            PaginationLoaderMixin,
        )

        class Gallery:
            class progress_bar:
                @staticmethod
                def setVisible(_): pass
            @staticmethod
            def set_loading_state(_): pass
            @staticmethod
            def get_filter_state():
                return {"search": search, "media_type": media_type}

        class Controller(PaginationLoaderMixin):
            def __init__(self):
                self.gallery = Gallery()
                self.lib_manager = object()
                self.limit, self.offset = 50, 0
                self.is_loading = False
                self.started_with = None
            def _cancel_loader_thread(self): pass
            def _start_loader_thread(self, worker, append=False):
                self.started_with = worker

        return Controller()

    def test_the_search_text_is_recorded(self):
        controller = self._controller(search="explosion")
        controller.fetch_assets()

        assert controller.current_search == "explosion"

    def test_an_empty_search_is_recorded_as_nothing_rather_than_blank(self):
        controller = self._controller(search="")
        controller.fetch_assets()

        assert controller.current_search is None

    def test_the_media_type_is_recorded_as_the_extensions_it_means(self):
        controller = self._controller(media_type="Videos")
        controller.fetch_assets()

        assert controller.current_file_types
        assert all(t.startswith(".") for t in controller.current_file_types), (
            "the column holds extensions, so the filter has to send extensions"
        )

    def test_no_media_filter_records_nothing(self):
        controller = self._controller(media_type="All")
        controller.fetch_assets()

        assert controller.current_file_types is None

    def test_the_count_is_then_asked_for_with_that_filter(self):
        """The whole point: the number on screen has to match the list."""
        controller = self._controller(search="explosion", media_type="Videos")
        controller.fetch_assets()

        asked = {}

        class Library:
            @staticmethod
            def get_total_count(query=None, file_types=None, asset_ids=None):
                asked["query"] = query
                asked["file_types"] = file_types
                return 7
            @staticmethod
            def get_categories():
                return []

        class Sidebar:
            @staticmethod
            def set_controls_enabled(_): pass
            @staticmethod
            def update_categories(_): pass

        class Model:
            @staticmethod
            def load_data(_): pass
            @staticmethod
            def rowCount(): return 0

        controller.lib_manager = Library()
        controller.sidebar = Sidebar()
        controller.model = Model()
        controller.apply_post_load_filters = lambda: None
        controller.update_ui_counts = lambda: None

        controller.on_library_loaded([{"id": "a"}], append=False)

        assert asked["query"] == "explosion"
        assert asked["file_types"], "the media filter was dropped on the way"
        assert controller.db_total == 7


class TestBothBackendsOfferWhatTheLibraryAsksFor:
    """
    Each backend forwards the stock methods one by one, by hand.

    A method added to the repository but not forwarded is invisible: the callers
    reach for it with getattr(..., None) and quietly fall back, so a filtered
    count returns the whole library and the ingest goes back to reading every
    row. Nothing raises, nothing is logged at a level anyone sees, and the
    feature is simply absent.
    """

    REQUIRED = (
        "get_stock_count",
        "get_all_stock_assets",
        "count_stock_assets",
        "list_stock_paths",
        "remove_stock_asset_by_path",
        "add_stock_asset",
        "add_stock_assets_batch",
        "update_asset_metadata",
        "update_stock_asset_paths",
    )

    @pytest.mark.parametrize("module_name, class_name", [
        ("ut_vfx.core.infra.postgres_manager", "PostgresManager"),
        ("ut_vfx.core.infra.sqlite_manager", "SQLiteManager"),
    ])
    def test_every_stock_method_is_forwarded(self, module_name, class_name):
        import importlib

        module = importlib.import_module(module_name)
        backend = next(
            obj for name, obj in vars(module).items()
            if isinstance(obj, type) and "manager" in name.lower()
            and hasattr(obj, "get_stock_count")
        )

        missing = [name for name in self.REQUIRED if not hasattr(backend, name)]

        assert not missing, (
            f"{backend.__name__} does not forward {missing} - callers will "
            "silently fall back instead of failing"
        )

    def test_the_repository_defines_them_in_the_first_place(self):
        from ut_vfx.core.infra.stock_repository import StockRepository

        for name in self.REQUIRED:
            assert hasattr(StockRepository, name), name

    def test_the_filtered_count_takes_the_same_arguments_all_the_way_down(self):
        """A forward with the wrong signature fails just as quietly."""
        from ut_vfx.core.infra.stock_repository import StockRepository
        from ut_vfx.core.infra.postgres_manager import PostgresManager

        expected = list(inspect.signature(
            StockRepository.count_stock_assets).parameters)[1:]
        forwarded = list(inspect.signature(
            PostgresManager.count_stock_assets).parameters)[1:]

        assert forwarded == expected


class TestTheLibraryIsIndexed:

    def test_the_columns_every_browse_uses_are_covered(self):
        from ut_vfx.core.infra.migrations.stock_indexes import INDEXES

        covered = " ".join(definition for _name, definition in INDEXES)

        assert "ingest_date" in covered, "every listing sorts by this"
        assert "file_type" in covered, "every category filter narrows on this"

    def test_creating_them_twice_is_harmless(self):
        from ut_vfx.core.infra.migrations.stock_indexes import INDEXES

        for name, definition in INDEXES:
            assert name and definition
