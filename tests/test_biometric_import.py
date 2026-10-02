"""
One importer for every biometric machine.

Three exports of three different shapes, none of them from any particular
device, all reduced to the same days. If the guesser stops recognising one of
these, a studio with that kind of machine is back to typing attendance in.
"""

import datetime as dt

import pytest

from slate.core.domain import biometric_import as bio


KNOWN = ["EMP0001", "EMP0002", "EMP0003"]


# Shape A: one timestamp column, an in/out state, comma separated, ISO dates.
SHAPE_A = """AC-No.,Name,Time,State
EMP0001,Priya,2026-09-01 09:12:33,C/In
EMP0001,Priya,2026-09-01 13:02:10,C/Out
EMP0001,Priya,2026-09-01 13:40:05,C/In
EMP0001,Priya,2026-09-01 18:45:59,C/Out
EMP0002,Rahul,2026-09-01 10:01:00,C/In
EMP0002,Rahul,2026-09-01 19:30:00,C/Out
9999,Guest,2026-09-01 11:00:00,C/In
"""

# Shape B: date and time apart, day-first dates, semicolons, IN/OUT words.
SHAPE_B = """Employee Code;Date;Time;Punch
EMP0001;01/09/2026;09:12;IN
EMP0001;01/09/2026;18:46;OUT
EMP0003;02/09/2026;08:55;IN
EMP0003;02/09/2026;17:10;OUT
"""

# Shape C: bare punches, no direction, no header words worth anything,
# tab separated, 12-hour clock.
SHAPE_C = "1\tEMP0002\t01-09-2026 09:00 AM\n2\tEMP0002\t01-09-2026 06:15 PM\n3\tEMP0002\t01-09-2026 01:00 PM\n"


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestReadingAndGuessing:

    def test_shape_a_is_understood(self, tmp_path):
        header, rows = bio.read_table(write(tmp_path, "a.csv", SHAPE_A))
        m = bio.guess_mapping(header, rows)
        assert header[m.code] == "AC-No."
        assert header[m.datetime] == "Time"
        assert header[m.direction] == "State"
        assert header[m.name] == "Name"
        assert not m.problems()

    def test_shape_b_is_understood(self, tmp_path):
        header, rows = bio.read_table(write(tmp_path, "b.csv", SHAPE_B))
        m = bio.guess_mapping(header, rows)
        assert header[m.code] == "Employee Code"
        assert header[m.date] == "Date"
        assert header[m.time] == "Time"
        assert header[m.direction] == "Punch"
        assert not m.problems()

    def test_shape_c_is_understood_without_a_header(self, tmp_path):
        header, rows = bio.read_table(write(tmp_path, "c.txt", SHAPE_C))
        assert header == ["Column 1", "Column 2", "Column 3"]
        assert len(rows) == 3
        m = bio.guess_mapping(header, rows)
        assert m.code == 1, "the repeating short column is the code, not the row number"
        assert m.datetime == 2
        assert m.direction is None
        assert not m.problems()

    def test_an_old_xls_is_refused_with_advice(self, tmp_path):
        path = tmp_path / "export.xls"
        path.write_bytes(b"\xd0\xcf\x11\xe0 not really")
        with pytest.raises(ValueError, match="save it as CSV"):
            bio.read_table(path)

    def test_xlsx_is_read(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl")
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.append(["Emp ID", "Log Time", "Type"])
        sheet.append(["EMP0001", dt.datetime(2026, 9, 3, 9, 5), "IN"])
        sheet.append(["EMP0001", dt.datetime(2026, 9, 3, 18, 0), "OUT"])
        path = tmp_path / "export.xlsx"
        book.save(path)

        header, rows = bio.read_table(path)
        m = bio.guess_mapping(header, rows)
        assert header[m.code] == "Emp ID"
        assert header[m.datetime] == "Log Time"
        punches, skipped = bio.extract_punches(header, rows, m)
        assert len(punches) == 2 and not skipped


class TestReducingToDays:

    def _days(self, tmp_path, name, text, **kw):
        header, rows = bio.read_table(write(tmp_path, name, text))
        m = bio.guess_mapping(header, rows)
        punches, skipped = bio.extract_punches(header, rows, m)
        assert not skipped
        return bio.reduce_to_days(punches, KNOWN, **kw)

    def test_earliest_in_and_latest_out(self, tmp_path):
        days, unknown = self._days(tmp_path, "a.csv", SHAPE_A)
        by_user = {d.user_id: d for d in days}
        assert by_user["EMP0001"].punch_in == "09:12:33"
        assert by_user["EMP0001"].punch_out == "18:45:59"
        assert by_user["EMP0001"].punches == 4
        assert by_user["EMP0002"].punch_out == "19:30:00"

    def test_unknown_codes_are_counted_not_guessed(self, tmp_path):
        days, unknown = self._days(tmp_path, "a.csv", SHAPE_A)
        assert unknown == {"9999": 1}
        assert all(d.user_id != "9999" for d in days)

    def test_a_code_map_resolves_an_unknown_code(self, tmp_path):
        days, unknown = self._days(tmp_path, "a.csv", SHAPE_A, code_map={"9999": "EMP0003"})
        assert unknown == {}
        assert any(d.user_id == "EMP0003" and d.punch_in == "11:00:00" for d in days)

    def test_day_first_dates_and_separate_time_column(self, tmp_path):
        days, _ = self._days(tmp_path, "b.csv", SHAPE_B)
        assert {(d.user_id, d.day.isoformat()) for d in days} == {
            ("EMP0001", "2026-09-01"), ("EMP0003", "2026-09-02")}
        assert next(d for d in days if d.user_id == "EMP0003").punch_out == "17:10:00"

    def test_bare_punches_without_direction(self, tmp_path):
        days, _ = self._days(tmp_path, "c.txt", SHAPE_C)
        assert len(days) == 1
        assert days[0].punch_in == "09:00:00"
        assert days[0].punch_out == "18:15:00", "the 1 PM punch is neither first nor last"

    def test_codes_match_case_insensitively(self, tmp_path):
        header, rows = bio.read_table(write(tmp_path, "lc.csv", SHAPE_A.replace("EMP0001", "emp0001")))
        m = bio.guess_mapping(header, rows)
        punches, _ = bio.extract_punches(header, rows, m)
        days, unknown = bio.reduce_to_days(punches, KNOWN)
        assert any(d.user_id == "EMP0001" for d in days)


class TestWritingIntoAttendance:

    @pytest.fixture
    def attendance(self, mock_db):
        from slate.core.domain.central_attendance import CentralAttendance
        return CentralAttendance(db=mock_db)

    def _import(self, tmp_path, attendance, text, name="a.csv"):
        header, rows = bio.read_table(write(tmp_path, name, text))
        m = bio.guess_mapping(header, rows)
        punches, _ = bio.extract_punches(header, rows, m)
        days, _ = bio.reduce_to_days(punches, KNOWN)
        return bio.apply_days(days, attendance, source=name)

    def test_days_are_written_and_read_back(self, tmp_path, attendance):
        result = self._import(tmp_path, attendance, SHAPE_A)
        assert result["written"] == 2 and result["failed"] == 0

        day = attendance.get_day("EMP0001", dt.date(2026, 9, 1))
        assert day["punch_in"] == "09:12:33"
        assert day["punch_out"] == "18:45:59"

    def test_importing_the_same_file_twice_changes_nothing(self, tmp_path, attendance):
        self._import(tmp_path, attendance, SHAPE_A)
        again = self._import(tmp_path, attendance, SHAPE_A)
        assert again["written"] == 0
        assert again["unchanged"] == 2

    def test_a_workstation_punch_and_a_machine_punch_merge(self, tmp_path, attendance):
        # The workstation saw the person at 09:30 and 18:00; the machine at the
        # door saw them at 09:12 and 18:45. The day is 09:12 to 18:45.
        ok, _ = attendance.write_day("EMP0001", dt.date(2026, 9, 1), "09:30:00", "18:00:00",
                                     pc_name="WS-07")
        assert ok
        self._import(tmp_path, attendance, SHAPE_A)
        day = attendance.get_day("EMP0001", dt.date(2026, 9, 1))
        assert (day["punch_in"], day["punch_out"]) == ("09:12:33", "18:45:59")

    def test_the_record_says_it_came_from_the_machine(self, tmp_path, attendance):
        self._import(tmp_path, attendance, SHAPE_A, name="door_2026-09.csv")
        day = attendance.get_day("EMP0002", dt.date(2026, 9, 1))
        assert day["pc_name"] == "BIOMETRIC"
        assert "door_2026-09.csv" in str(day["metadata"])


class TestProfiles:

    def test_a_mapping_is_remembered_for_the_same_header(self, tmp_path):
        header, rows = bio.read_table(write(tmp_path, "a.csv", SHAPE_A))
        m = bio.guess_mapping(header, rows)
        m.set_role(1, "ignore")             # HR decided the name column is noise
        store = tmp_path / "profiles.json"
        assert bio.save_profile(header, m, {"9999": "EMP0003"}, path=store, label="door")

        found = bio.find_profile(header, bio.load_profiles(store))
        assert found["label"] == "door"
        assert bio.Mapping.from_dict(found["mapping"]).name is None
        assert found["code_map"] == {"9999": "EMP0003"}

    def test_a_different_header_is_a_different_machine(self, tmp_path):
        header_a, rows_a = bio.read_table(write(tmp_path, "a.csv", SHAPE_A))
        header_b, _ = bio.read_table(write(tmp_path, "b.csv", SHAPE_B))
        store = tmp_path / "profiles.json"
        bio.save_profile(header_a, bio.guess_mapping(header_a, rows_a), {}, path=store)
        assert bio.find_profile(header_b, bio.load_profiles(store)) is None


# ------------------------------------------------------------ HR-038 / HR-002

NIGHT = """AC-No.,Name,Time,State
EMP0001,Priya,2026-09-13 23:30:00,C/In
EMP0001,Priya,2026-09-14 06:30:00,C/Out
EMP0002,Rahul,2026-09-13 09:00:00,C/In
EMP0002,Rahul,2026-09-13 18:00:00,C/Out
"""


def test_a_night_shift_is_one_day_with_its_morning_out(tmp_path):
    header, rows = bio.read_table(write(tmp_path, "n.csv", NIGHT))
    punches, _ = bio.extract_punches(header, rows, bio.guess_mapping(header, rows))
    days, _ = bio.reduce_to_days(punches, KNOWN)
    priya = [d for d in days if d.user_id == "EMP0001"]
    assert len(priya) == 1
    assert priya[0].day == dt.date(2026, 9, 13)
    assert (priya[0].punch_in, priya[0].punch_out, priya[0].overnight) == ("23:30:00", "06:30:00", True)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
def test_days_a_workstation_already_recorded_are_merged_on_both_databases(request, tmp_path, backend):
    from slate.core.domain.central_attendance import CentralAttendance
    db = request.getfixturevalue("mock_db" if backend == "sqlite" else "pg_db")
    attendance = CentralAttendance(db=db)
    attendance.write_day("EMP0001", dt.date(2026, 9, 1), "09:30:00", "18:00:00", pc_name="WS-07")
    header, rows = bio.read_table(write(tmp_path, "a.csv", SHAPE_A))
    punches, _ = bio.extract_punches(header, rows, bio.guess_mapping(header, rows))
    days, _ = bio.reduce_to_days(punches, KNOWN)
    first = bio.apply_days(days, attendance, source="a.csv")
    assert first["failed"] == 0 and first["written"] == 2
    again = bio.apply_days(days, attendance, source="a.csv")
    assert again["failed"] == 0 and again["written"] == 0 and again["unchanged"] == 2
