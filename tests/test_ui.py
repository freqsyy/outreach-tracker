import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_palette_constants_importable():
    import gordon_desktop as g
    assert g.BG == "#070710"
    assert g.NEON_PURPLE == "#a855f7"
    assert g.NEON_CYAN == "#22d3ee"
    assert g.NEON_PINK == "#f472b6"
    assert set(g.STATUS_COLORS.keys()) == {
        "pending", "sent", "replied", "hired", "rejected", "bounced"
    }
