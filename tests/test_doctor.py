"""
The diagnostic has to be right about disagreement, or it is worse than nothing.

A check that reports "ok" when it could not really tell is the same failure this
command was written to expose, one level up. So these tests are mostly about the
unhappy cases: a stale pool, an empty cluster, a manifest that names a package
which is not there.
"""

import json

import pytest

from slate import doctor


@pytest.fixture
def report():
    r = doctor.Report()
    r.section("test")
    return r


class TestTheReportItself:

    def test_a_clean_report_passes(self, report):
        report.add(doctor.OK, "something", "fine")
        assert report.failed == []
        assert report.warned == []

    def test_failures_are_collected_not_just_printed(self, report):
        report.add(doctor.OK, "a")
        report.add(doctor.FAIL, "b", "broken")
        report.add(doctor.WARN, "c", "odd")
        assert len(report.failed) == 1
        assert len(report.warned) == 1

    def test_a_check_that_raises_is_reported_rather_than_swallowed(self, monkeypatch, capsys):
        """
        The one thing this command must never do is hide an error, including
        its own.
        """
        def exploding(report, cfg):
            raise RuntimeError("the check is broken")

        monkeypatch.setattr(doctor, "check_database", exploding)
        monkeypatch.setattr(doctor, "check_server", lambda r, c: None)
        monkeypatch.setattr(doctor, "check_pool", lambda r, c: None)
        monkeypatch.setattr(doctor, "check_updates", lambda r, c: None)
        monkeypatch.setattr(doctor, "check_configuration", lambda r: {})

        code = doctor.main()
        out = capsys.readouterr().out
        assert code == 1
        assert "the check is broken" in out


class TestCredentialsAreNotPrinted:

    @pytest.mark.parametrize("key", ["db_password", "admin_password",
                                     "api_token", "secret_key"])
    def test_a_secret_is_described_never_shown(self, key):
        shown = doctor.describe(key, "hunter2!")
        assert "hunter2" not in shown
        assert "8 characters" in shown

    def test_an_unset_secret_says_so(self):
        assert doctor.describe("db_password", "") == "not set"

    def test_ordinary_settings_are_shown_plainly(self):
        assert doctor.describe("db_name", "ut_vfx") == "'ut_vfx'"


class TestThePoolCheck:

    def _write(self, tmp_path, monkeypatch, body):
        pgb = tmp_path / "pgbouncer"
        pgb.mkdir()
        (pgb / "pgbouncer.ini").write_text(body, encoding="utf-8")
        # doctor.py lives in slate/, so it looks one level up for pgbouncer/
        monkeypatch.setattr(doctor, "__file__", str(tmp_path / "slate" / "doctor.py"))

    def test_a_matching_pool_is_fine(self, tmp_path, monkeypatch, report):
        self._write(tmp_path, monkeypatch,
                    "[databases]\nut_vfx = host=127.0.0.1 dbname=ut_vfx user=app\n")
        doctor.check_pool(report, {"db_name": "ut_vfx"})
        assert report.failed == []

    def test_a_pool_publishing_the_wrong_name_is_a_failure(self, tmp_path, monkeypatch, report):
        """
        The real one: the file said 'slate' while every client asked for
        'ut_vfx', so anything using the pooler port was refused.
        """
        self._write(tmp_path, monkeypatch,
                    "[databases]\nslate = host=127.0.0.1 dbname=ut_vfx user=app\n")
        doctor.check_pool(report, {"db_name": "ut_vfx"})
        assert report.failed, "a pool publishing the wrong name was reported as fine"

    def test_a_pool_pointed_at_the_wrong_database_is_a_failure(self, tmp_path, monkeypatch, report):
        self._write(tmp_path, monkeypatch,
                    "[databases]\nut_vfx = host=127.0.0.1 dbname=slate user=app\n")
        doctor.check_pool(report, {"db_name": "ut_vfx"})
        assert any("backend" in f[2] for f in report.failed)

    def test_comments_are_not_mistaken_for_entries(self, tmp_path, monkeypatch, report):
        self._write(tmp_path, monkeypatch,
                    "[databases]\n; ut_vfx = something in a comment\n"
                    "ut_vfx = host=127.0.0.1 dbname=ut_vfx user=app\n")
        doctor.check_pool(report, {"db_name": "ut_vfx"})
        assert report.failed == []


class TestTheServerCheck:

    def _cluster(self, tmp_path, databases):
        data = tmp_path / "LocalDatabase"
        (data / "base").mkdir(parents=True)
        (data / "PG_VERSION").write_text("14", encoding="utf-8")
        for i in range(databases):
            (data / "base" / str(i + 1)).mkdir()
        return data

    def _settings(self, tmp_path, monkeypatch, db_path, port=5440):
        appdata = tmp_path / "appdata"
        (appdata / "Slate_Central").mkdir(parents=True)
        (appdata / "Slate_Central" / "slate_server_config.json").write_text(
            json.dumps({"db_path": str(db_path), "port": port}), encoding="utf-8")
        monkeypatch.setenv("LOCALAPPDATA", str(appdata))

    def test_a_real_cluster_passes(self, tmp_path, monkeypatch, report):
        data = self._cluster(tmp_path, 5)
        self._settings(tmp_path, monkeypatch, data)
        doctor.check_server(report, {"db_port": 5440})
        assert report.failed == []

    def test_a_cluster_holding_only_templates_is_flagged(self, tmp_path, monkeypatch, report):
        """The signature of a database built by mistake."""
        data = self._cluster(tmp_path, 3)
        self._settings(tmp_path, monkeypatch, data)
        doctor.check_server(report, {"db_port": 5440})
        assert report.warned

    def test_missing_settings_are_a_failure_not_a_first_run(self, tmp_path, monkeypatch, report):
        appdata = tmp_path / "appdata"
        (appdata / "Slate_Central").mkdir(parents=True)
        monkeypatch.setenv("LOCALAPPDATA", str(appdata))
        doctor.check_server(report, {})
        assert report.failed

    def test_a_port_disagreement_is_caught(self, tmp_path, monkeypatch, report):
        data = self._cluster(tmp_path, 5)
        self._settings(tmp_path, monkeypatch, data, port=5441)
        doctor.check_server(report, {"db_port": 5440})
        assert any("port" in f[2] for f in report.failed)

    def test_a_path_that_is_not_a_cluster_is_caught(self, tmp_path, monkeypatch, report):
        empty = tmp_path / "not_a_cluster"
        empty.mkdir()
        self._settings(tmp_path, monkeypatch, empty)
        doctor.check_server(report, {"db_port": 5440})
        assert report.failed


class TestTheUpdateCheck:

    def _channel(self, tmp_path):
        releases = tmp_path / "Updates" / "releases"
        releases.mkdir(parents=True)
        return releases

    def test_a_manifest_naming_a_missing_package_is_a_failure(self, tmp_path, report):
        from slate.core.updater.manifest import build, manifest_name

        releases = self._channel(tmp_path)
        (releases / manifest_name("client")).write_text(json.dumps(build(
            version="1.0.0", package_name="nothing_here.zip",
            hash_sha256="a" * 64, target="client")), encoding="utf-8")

        doctor.check_updates(report, {"SERVER_ROOT": str(tmp_path)})
        assert any("package" in f[2] for f in report.failed)

    def test_a_complete_release_passes(self, tmp_path, report):
        from slate.core.updater.manifest import build, manifest_name

        releases = self._channel(tmp_path)
        (releases / "Slate_Client_Update.zip").write_bytes(b"x")
        (releases / manifest_name("client")).write_text(json.dumps(build(
            version="1.0.0", package_name="Slate_Client_Update.zip",
            hash_sha256="a" * 64, target="client")), encoding="utf-8")

        doctor.check_updates(report, {"SERVER_ROOT": str(tmp_path)})
        assert report.failed == []

    def test_an_unreachable_central_folder_is_a_failure(self, tmp_path, report):
        doctor.check_updates(report, {"SERVER_ROOT": str(tmp_path / "nowhere")})
        assert report.failed

    def test_no_channel_yet_is_a_warning_not_a_failure(self, tmp_path, report):
        doctor.check_updates(report, {"SERVER_ROOT": str(tmp_path)})
        assert report.failed == []
        assert report.warned
