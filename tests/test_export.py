"""Unit tests for mtgsets.export — the Moxfield CSV writer.

The canonical example row from docs/DESIGN.md is reproduced byte-for-byte so the
export format can never silently drift from the spec.
"""

from __future__ import annotations

from mtgsets import export

# The canonical header and example data row from docs/DESIGN.md.
EXPECTED_HEADER = (
    "Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Collector Number"
)
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
