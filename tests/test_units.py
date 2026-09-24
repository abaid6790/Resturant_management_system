import pytest

from app.core.errors import BusinessRuleError
from app.core.units import convert
from app.extensions import db
from app.models.inventory import UnitOfMeasure
from app.services import catalog


def test_seed_is_idempotent(clean):
    with clean.app_context():
        assert catalog.seed_units() == 7
        assert catalog.seed_units() == 0
        assert db.session.scalar(db.select(db.func.count(UnitOfMeasure.id))) == 7


def test_conversion_within_kind(clean):
    with clean.app_context():
        catalog.seed_units()
        g, kg, ml, liter = (db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == c))
                       for c in ("g", "kg", "ml", "l"))
        assert convert("1500", g, kg) == 1.5 or str(convert("1500", g, kg)) == "1.5000"
        assert str(convert("2", kg, g)) == "2000.0000"
        assert str(convert("0.5", liter, ml)) == "500.0000"


def test_conversion_rejects_different_kinds(clean):
    with clean.app_context():
        catalog.seed_units()
        g, ml = (db.session.scalar(db.select(UnitOfMeasure).where(UnitOfMeasure.code == c))
                for c in ("g", "ml"))
        with pytest.raises(BusinessRuleError, match="different kinds"):
            convert("100", g, ml)
