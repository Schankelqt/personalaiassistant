from personal_ai_os.core.file_ingest import format_ingest_for_prompt, ingest_file


def test_ingest_md() -> None:
    data = b"# Title\n\nHello **world**"
    r = ingest_file(data, "note.md")
    assert r.error is None
    assert "Title" in r.text
    assert r.kind == "text"


def test_ingest_csv() -> None:
    data = b"a,b\n1,2\n"
    r = ingest_file(data, "t.csv")
    assert "1" in r.text
    assert r.kind == "csv"


def test_format_error() -> None:
    r = ingest_file(b"x", "file.xyz")
    s = format_ingest_for_prompt(r)
    assert "не поддерживается" in s or "Ошибка" in s
