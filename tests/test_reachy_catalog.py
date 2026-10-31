import pytest

from forge_puppeteer.reachy_catalog import discover_recordings


class Library:
    def __init__(self, names):
        self.names = names

    def list_moves(self):
        return self.names

    def get(self, name):
        assert name in self.names
        return type("Move", (), {"duration": 1.5})()


def test_catalog_tracks_library_contents():
    one = discover_recordings(Library(["happy"]), source="dataset", revision="abc")
    two = discover_recordings(Library(["new", "happy"]), source="dataset", revision="def")
    assert [item.id for item in one] == ["reachy/happy"]
    assert [item.id for item in two] == ["reachy/happy", "reachy/new"]
    assert two[0].revision == "def"


def test_catalog_rejects_duplicate_names():
    with pytest.raises(ValueError):
        discover_recordings(Library(["same", "same"]), source="dataset", revision="abc")
