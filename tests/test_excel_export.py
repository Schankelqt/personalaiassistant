from personal_ai_os.core.excel_export import build_xlsx_from_spec


def test_build_xlsx_basic() -> None:
    data, name = build_xlsx_from_spec(
        {
            "file_name": "test_report",
            "sheets": [
                {
                    "sheet_name": "Данные",
                    "headers": ["A", "B"],
                    "rows": [[1, 2], ["x", "y"]],
                }
            ],
        }
    )
    assert name == "test_report.xlsx"
    assert data[:2] == b"PK"  # zip / xlsx signature


def test_build_xlsx_rejects_too_many_sheets() -> None:
    try:
        build_xlsx_from_spec(
            {
                "file_name": "x",
                "sheets": [{"rows": []}] * 11,
            }
        )
    except ValueError as e:
        assert "10" in str(e)
    else:
        raise AssertionError("expected ValueError")
