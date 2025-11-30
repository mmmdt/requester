import pytest

@pytest.fixture
def placeholder_dir(tmp_path):
    d = tmp_path / "placeholders"
    d.mkdir()
    (d / "name.txt").write_text("alice\nbob\ncharlie", encoding="utf-8")
    (d / "id").write_text("101\n102", encoding="utf-8")
    return d
