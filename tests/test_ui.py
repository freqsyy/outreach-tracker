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


from PySide6.QtWidgets import QGraphicsDropShadowEffect
import gordon_desktop as g


def test_neon_card_set_value(qapp):
    card = g.NeonCard("Sent", g.NEON_CYAN)
    card.set_value("42")
    assert card.num.text() == "42"


def test_neon_card_has_glow(qapp):
    card = g.NeonCard("Total", g.NEON_PURPLE)
    assert isinstance(card.graphicsEffect(), QGraphicsDropShadowEffect)


def test_neon_title_gradient_text(qapp):
    t = g.NeonTitle("ПУЛЬТ ГОРДОНА")
    assert t._text == "ПУЛЬТ ГОРДОНА"
    assert isinstance(t.graphicsEffect(), QGraphicsDropShadowEffect)


def test_neon_button_has_glow(qapp):
    b = g.NeonButton("Запустить", g.NEON_CYAN)
    assert isinstance(b.graphicsEffect(), QGraphicsDropShadowEffect)


def test_neon_button_hover_increases_blur(qapp):
    b = g.NeonButton("Запустить", g.NEON_CYAN)
    base = b.graphicsEffect().blurRadius()
    b.enterEvent(None)
    assert b.graphicsEffect().blurRadius() > base
    b.leaveEvent(None)
    assert b.graphicsEffect().blurRadius() == base
