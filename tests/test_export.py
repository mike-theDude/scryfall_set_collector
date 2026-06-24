"""Unit tests for mtgsets.export — the Moxfield CSV writer.

The canonical example row from docs/DESIGN.md is reproduced byte-for-byte so the
export format can never silently drift from the spec.
"""

from __future__ import annotations

import json

from mtgsets import export

# The canonical header and example data row from docs/DESIGN.md.
EXPECTED_HEADER = "Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Collector Number"
EXPECTED_ROW = '1,0,"Boseiju, Who Endures",NEO,Near Mint,English,,Full Set: NEO,266'


def boseiju_entry(**overrides) -> dict:
    """The DESIGN.md example card as a joined collection_entries+cards record."""
    entry = {
        "name": "Boseiju, Who Endures",
        "set_code": "neo",
        "collector_number": "266",
        "quantity": 1,
        "condition": "Near Mint",
        "language": "English",
        "foil": 0,
        "source_type": "full_set",
        "source_set_code": "neo",
    }
    entry.update(overrides)
    return entry


# -- _tag_for ---------------------------------------------------------------------


def test_tag_for_set_generated() -> None:
    assert export._tag_for(boseiju_entry()) == "Full Set: NEO"


def test_tag_for_manual_single_is_empty() -> None:
    assert export._tag_for(boseiju_entry(source_set_code=None)) == ""
    assert export._tag_for(boseiju_entry(source_set_code="")) == ""


# -- entry_to_row -----------------------------------------------------------------


def test_entry_to_row_column_order_and_defaults() -> None:
    assert export.entry_to_row(boseiju_entry()) == [
        1,  # Count
        0,  # Tradelist Count — always 0
        "Boseiju, Who Endures",  # Name
        "NEO",  # Edition — uppercased
        "Near Mint",  # Condition
        "English",  # Language
        "",  # Foil — empty for nonfoil
        "Full Set: NEO",  # Tags
        "266",  # Collector Number
    ]


def test_entry_to_row_foil() -> None:
    assert export.entry_to_row(boseiju_entry(foil=1))[6] == "foil"
    assert export.entry_to_row(boseiju_entry(foil=0))[6] == ""


def test_entry_to_row_edition_uppercased() -> None:
    assert export.entry_to_row(boseiju_entry(set_code="neo"))[3] == "NEO"


def test_entry_to_row_handles_missing_set_code() -> None:
    assert export.entry_to_row(boseiju_entry(set_code=None))[3] == ""


def test_entry_to_row_manual_single_has_no_tag() -> None:
    assert export.entry_to_row(boseiju_entry(source_set_code=None))[7] == ""


# -- write_moxfield_csv -----------------------------------------------------------


def test_write_reproduces_design_example_byte_for_byte(tmp_path) -> None:
    dest = tmp_path / "moxfield.csv"
    count = export.write_moxfield_csv([boseiju_entry()], dest)

    assert count == 1
    # splitlines() normalises csv's \r\n terminator; the content must match the
    # docs/DESIGN.md header and example row exactly, including minimal quoting
    # (only Name is quoted, because it contains a comma).
    lines = dest.read_text(encoding="utf-8").splitlines()
    assert lines == [EXPECTED_HEADER, EXPECTED_ROW]


def test_write_uses_crlf_line_terminator(tmp_path) -> None:
    # csv.writer's default terminator; assert the raw bytes really carry it.
    dest = tmp_path / "moxfield.csv"
    export.write_moxfield_csv([boseiju_entry()], dest)
    assert dest.read_bytes().startswith(EXPECTED_HEADER.encode() + b"\r\n")


def test_write_returns_row_count(tmp_path) -> None:
    dest = tmp_path / "moxfield.csv"
    entries = [boseiju_entry(), boseiju_entry(name="Plains", collector_number="283")]
    assert export.write_moxfield_csv(entries, dest) == 2
    assert len(dest.read_text(encoding="utf-8").splitlines()) == 3  # header + 2 rows


def test_write_creates_parent_dirs(tmp_path) -> None:
    dest = tmp_path / "nested" / "deeper" / "moxfield.csv"
    export.write_moxfield_csv([boseiju_entry()], dest)
    assert dest.exists()


def test_write_empty_collection_writes_header_only(tmp_path) -> None:
    dest = tmp_path / "moxfield.csv"
    count = export.write_moxfield_csv([], dest)
    assert count == 0
    assert dest.read_text(encoding="utf-8").splitlines() == [EXPECTED_HEADER]


def test_header_columns_match_constant(tmp_path) -> None:
    assert ",".join(export.MOXFIELD_COLUMNS) == EXPECTED_HEADER


# -- parse_moxfield_csv (import, issue #61) ---------------------------------------


def parse_one(*rows: str) -> list[export.ImportRow]:
    """Parse a header plus the given data rows."""
    return export.parse_moxfield_csv([EXPECTED_HEADER, *rows])


def test_parse_basic_row_fields() -> None:
    (row,) = parse_one("2,0,Ao the Dawn Sky,NEO,Lightly Played,Japanese,foil,,2")
    assert row.edition == "neo"  # lowercased
    assert row.collector_number == "2"
    assert row.quantity == 2
    assert row.condition == "Lightly Played"
    assert row.language == "Japanese"
    assert row.foil == 1
    assert row.is_full_set is False
    assert row.error is None


def test_parse_defaults_for_blank_optional_fields() -> None:
    # Blank Count/Condition/Language fall back to sane defaults; blank Foil -> nonfoil.
    (row,) = parse_one(",0,Plains,NEO,,,,, 283")
    assert row.quantity == 1
    assert row.condition == "Near Mint"
    assert row.language == "English"
    assert row.foil == 0
    assert row.collector_number == "283"  # surrounding whitespace stripped


def test_parse_invalid_count_falls_back_to_one() -> None:
    (row,) = parse_one("abc,0,Plains,NEO,Near Mint,English,,,283")
    assert row.quantity == 1


def test_parse_full_set_tag_detected() -> None:
    (row,) = parse_one('1,0,"Boseiju, Who Endures",NEO,Near Mint,English,,Full Set: NEO,266')
    assert row.is_full_set is True


def test_parse_missing_edition_or_number_is_error() -> None:
    (no_number,) = parse_one("1,0,Plains,NEO,Near Mint,English,,,")
    assert no_number.error is not None
    (no_edition,) = parse_one("1,0,Plains,,Near Mint,English,,,283")
    assert no_edition.error is not None


def test_parse_is_case_insensitive_on_headers_and_tolerates_extra_columns() -> None:
    rows = export.parse_moxfield_csv(
        ["edition,collector number,count,foil,extra", "neo,2,3,Foil,ignored"]
    )
    assert rows[0].edition == "neo"
    assert rows[0].collector_number == "2"
    assert rows[0].quantity == 3
    assert rows[0].foil == 1


def test_parse_line_numbers_are_data_relative() -> None:
    rows = parse_one(
        "1,0,Ao,NEO,Near Mint,English,,,2",
        "1,0,Plains,NEO,Near Mint,English,,,283",
    )
    assert [r.line for r in rows] == [1, 2]


def test_parse_empty_input_is_empty_list() -> None:
    assert export.parse_moxfield_csv([EXPECTED_HEADER]) == []
    assert export.parse_moxfield_csv([]) == []


# -- JSON snapshot (issue #71) ----------------------------------------------------


def owned_set(**overrides) -> dict:
    """A list_owned_sets-shaped owned set record."""
    row = {
        "set_code": "neo",
        "set_name": "Kamigawa: Neon Dynasty",
        "added_at": "2024-02-01T00:00:00+00:00",
    }
    row.update(overrides)
    return row


# The canonical JSON snapshot — one owned set + the DESIGN.md example card, exact bytes.
EXPECTED_JSON = """\
{
  "version": 1,
  "owned_sets": [
    {
      "set_code": "neo",
      "set_name": "Kamigawa: Neon Dynasty",
      "added_at": "2024-02-01T00:00:00+00:00"
    }
  ],
  "entries": [
    {
      "name": "Boseiju, Who Endures",
      "set_code": "neo",
      "collector_number": "266",
      "quantity": 1,
      "condition": "Near Mint",
      "language": "English",
      "foil": false,
      "source_type": "full_set",
      "source_set_code": "neo"
    }
  ]
}
"""


def test_collection_to_json_byte_for_byte() -> None:
    assert export.collection_to_json([owned_set()], [boseiju_entry()]) == EXPECTED_JSON


def test_collection_to_json_foil_is_bool() -> None:
    doc = json.loads(export.collection_to_json([], [boseiju_entry(foil=1)]))
    assert doc["entries"][0]["foil"] is True
    doc = json.loads(export.collection_to_json([], [boseiju_entry(foil=0)]))
    assert doc["entries"][0]["foil"] is False


def test_collection_to_json_lowercases_set_code() -> None:
    doc = json.loads(export.collection_to_json([], [boseiju_entry(set_code="NEO")]))
    assert doc["entries"][0]["set_code"] == "neo"


def test_collection_to_json_preserves_source_metadata() -> None:
    # The override/manual tagging the CSV collapses into a tag is kept verbatim.
    doc = json.loads(
        export.collection_to_json(
            [], [boseiju_entry(source_type="override", source_set_code="neo")]
        )
    )
    assert doc["entries"][0]["source_type"] == "override"
    assert doc["entries"][0]["source_set_code"] == "neo"


def test_collection_to_json_empty_is_valid() -> None:
    text = export.collection_to_json([], [])
    assert text.endswith("\n")
    assert json.loads(text) == {"version": 1, "owned_sets": [], "entries": []}


def test_write_collection_json_returns_entry_count_and_creates_dirs(tmp_path) -> None:
    dest = tmp_path / "nested" / "collection.json"
    count = export.write_collection_json([owned_set()], [boseiju_entry(), boseiju_entry()], dest)
    assert count == 2
    assert dest.exists()
    # File content matches the serializer exactly (trailing newline included).
    assert dest.read_text(encoding="utf-8") == export.collection_to_json(
        [owned_set()], [boseiju_entry(), boseiju_entry()]
    )
