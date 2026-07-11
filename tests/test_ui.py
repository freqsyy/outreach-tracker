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


def test_status_indicator_idle(qapp):
    s = g.StatusIndicator()
    assert s._active is False
    assert "не запущен" in s.label.text()


def test_status_indicator_active(qapp):
    s = g.StatusIndicator()
    s.set_active(True, "Гордон работает…")
    assert s._active is True
    assert "работает" in s.label.text()
    s.set_active(False)
    assert s._active is False
    assert "не запущен" in s.label.text()


def test_main_window_builds(qapp):
    w = g.GordonDesktop()
    assert w is not None
    assert hasattr(w, "status_ind") and isinstance(w.status_ind, g.StatusIndicator)
    assert len(w.cards) >= 9
    assert isinstance(w.cards["Sent"], g.NeonCard)
    assert isinstance(w.btn_start, g.NeonButton)


def test_combo_style_neon_border(qapp):
    w = g.GordonDesktop()
    s = w._combo_style()
    assert "qlineargradient" not in s  # должен быть плоский неон-бордер
    assert g.BORDER in s
    assert "rgba(34,211,238" in s or "#22d3ee" in s  # циан-акцент в выпадашке


def test_table_style_neon(qapp):
    w = g.GordonDesktop()
    s = w._table_style()
    assert g.BORDER in s
    assert "#22d3ee" in s or "qlineargradient" in s
