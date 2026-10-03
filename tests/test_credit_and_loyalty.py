"""Credit sales (customer ledger) and loyalty points (earn/redeem/clawback on refund)."""
from app.core.errors import BusinessRuleError, PermissionDeniedError
from app.core.money import D
from app.extensions import db
from app.models.inventory import InventoryItem
from app.models.pos import Customer
from app.services import customer_ledger
from app.services import pos as pos_svc
from app.services import refunds as refund_svc
from app.services import settings as settings_svc


def _actor(app, role="Super Admin"):
    """Assumes an app context is already active (every call site provides one): opening a
    SEPARATE nested app_context here and returning the object after it closes detaches it from
    the session, which only shows up once something lazy-loads (e.g. a non-super user's
    .branches, touched by allowed_branch_ids) rather than on every use."""
    from app.models.auth import User
    username = {"Super Admin": "actor", "Cashier": "cashier_no_credit"}[role]
    u = db.session.scalar(db.select(User).where(User.username == username))
    if u is None:
        from tests.helpers import make_user
        make_user(app, username, role, branches=() if role == "Super Admin" else [1],
                 all_branches=(role == "Super Admin"))
        u = db.session.scalar(db.select(User).where(User.username == username))
    return u


def _ready_order(app, x, qty="1"):
    db.session.get(InventoryItem, x["burger"]).tax_rate_pct = None  # use the (0%) global rate
    settings_svc.update({"tax.rate_pct": "0"}, _actor(app), keys=["tax.rate_pct"])
    order = pos_svc.start_order(_actor(app), {"order_type": "takeaway", "location_id": x["loc_a"]})
    pos_svc.add_line(_actor(app), order, {"item_id": x["burger"], "quantity": qty})
    db.session.commit()
    return order


def _customer(app, name="Regular Joe"):
    c = Customer(name=name)
    db.session.add(c)
    db.session.commit()
    return c.id


def test_credit_sale_requires_a_customer(pos):
    app, ids, x, p = pos
    with app.app_context():
        order = _ready_order(app, x)
        try:
            pos_svc.complete_sale(_actor(app), order, [{"method": "card", "amount": "1.00"}])
            raise AssertionError
        except BusinessRuleError as e:
            assert "select a customer" in e.message.lower()


def test_credit_sale_requires_permission(env, inv, pos):
    app, ids, x, p = pos
    from tests.helpers import make_user
    make_user(app, "cashier_no_credit", "Cashier", branches=[ids["A"]])
    with app.app_context():
        customer_id = _customer(app)
        order = pos_svc.start_order(_actor(app, "Cashier"), {"order_type": "takeaway",
                                                              "location_id": x["loc_a"]})
        pos_svc.add_line(_actor(app, "Cashier"), order, {"item_id": x["burger"], "quantity": "1"})
        pos_svc.set_customer(_actor(app, "Cashier"), order, customer_id)
        db.session.commit()
        try:
            pos_svc.complete_sale(_actor(app, "Cashier"), order, [{"method": "card", "amount": "1.00"}])
            raise AssertionError
        except PermissionDeniedError:
            pass


def test_credit_sale_posts_ledger_and_partial_status(pos):
    app, ids, x, p = pos
    with app.app_context():
        customer_id = _customer(app)
        order = _ready_order(app, x)  # total 8.99
        pos_svc.set_customer(_actor(app), order, customer_id)
        db.session.commit()
        invoice = pos_svc.complete_sale(_actor(app), order, [{"method": "card", "amount": "3.00"}])
        db.session.commit()
        assert invoice.status == "partial" and invoice.paid_total == D("3.00")
        assert customer_ledger.balance_of(customer_id) == D("5.99")

        full_credit_order = _ready_order(app, x)
        pos_svc.set_customer(_actor(app), full_credit_order, customer_id)
        db.session.commit()
        invoice2 = pos_svc.complete_sale(_actor(app), full_credit_order, [])
        db.session.commit()
        assert invoice2.status == "unpaid" and invoice2.paid_total == D("0.00")
        assert customer_ledger.balance_of(customer_id) == D("14.98")  # 5.99 + 8.99


def test_customer_payment_reduces_balance_and_cannot_overpay(pos):
    app, ids, x, p = pos
    with app.app_context():
        customer_id = _customer(app)
        order = _ready_order(app, x)
        pos_svc.set_customer(_actor(app), order, customer_id)
        db.session.commit()
        pos_svc.complete_sale(_actor(app), order, [])
        db.session.commit()
        customer = db.session.get(Customer, customer_id)
        customer_ledger.record_payment(_actor(app), customer, {"amount": "5.00",
                                       "branch_id": str(ids["A"])})
        db.session.commit()
        assert customer_ledger.balance_of(customer_id) == D("3.99")
        try:
            customer_ledger.record_payment(_actor(app), customer, {"amount": "100.00",
                                           "branch_id": str(ids["A"])})
            raise AssertionError
        except BusinessRuleError:
            pass


def test_loyalty_earned_on_sale_and_redeemed_as_discount(pos):
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"loyalty.enabled": "on", "loyalty.earn_rate": "1",
                             "loyalty.redemption_value": "0.01"}, _actor(app),
                            keys=["loyalty.enabled", "loyalty.earn_rate", "loyalty.redemption_value"])
        customer_id = _customer(app)
        order = _ready_order(app, x)  # 8.99 -> floor(8.99 * 1) = 8 points
        pos_svc.set_customer(_actor(app), order, customer_id)
        db.session.commit()
        pos_svc.complete_sale(_actor(app), order, [{"method": "card", "amount": "8.99"}])
        db.session.commit()
        customer = db.session.get(Customer, customer_id)
        assert customer.loyalty_points == 8

        order2 = _ready_order(app, x)
        pos_svc.set_customer(_actor(app), order2, customer_id)
        db.session.commit()
        # redeem all 8 points = $0.08 off; pay the rest in card
        invoice2 = pos_svc.complete_sale(_actor(app), order2, [{"method": "card", "amount": "8.91"}],
                                         redeem_points=8)
        db.session.commit()
        assert invoice2.status == "paid"
        customer = db.session.get(Customer, customer_id)
        # earn() is based on the pre-redemption total (totals.total), by design: redeeming
        # points on one visit doesn't reduce what's earned on that same visit. 8 redeemed, then
        # 8 earned again on the (undiscounted) 8.99 total -> back to 8.
        assert customer.loyalty_points == 8


def test_redeeming_more_points_than_owned_rejected(pos):
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"loyalty.enabled": "on"}, _actor(app), keys=["loyalty.enabled"])
        customer_id = _customer(app)
        order = _ready_order(app, x)
        pos_svc.set_customer(_actor(app), order, customer_id)
        db.session.commit()
        try:
            pos_svc.complete_sale(_actor(app), order, [{"method": "card", "amount": "8.99"}],
                                  redeem_points=100)
            raise AssertionError
        except BusinessRuleError as e:
            assert "only has" in e.message.lower()


def test_loyalty_clawed_back_on_refund(pos):
    app, ids, x, p = pos
    with app.app_context():
        settings_svc.update({"loyalty.enabled": "on", "loyalty.earn_rate": "1",
                             "tax.rate_pct": "0"}, _actor(app),
                            keys=["loyalty.enabled", "loyalty.earn_rate", "tax.rate_pct"])
        db.session.get(InventoryItem, x["burger"]).tax_rate_pct = None
        customer_id = _customer(app)
        order = _ready_order(app, x, qty="2")  # 17.98 -> 17 points
        pos_svc.set_customer(_actor(app), order, customer_id)
        db.session.commit()
        invoice = pos_svc.complete_sale(_actor(app), order, [{"method": "card", "amount": "17.98"}])
        db.session.commit()
        customer = db.session.get(Customer, customer_id)
        assert customer.loyalty_points == 17

        refund_svc.create_refund(_actor(app), invoice, {"reason": "one was wrong",
                                 "lines": [{"order_line_id": invoice.order.lines[0].id, "quantity": "1"}],
                                 "payments": [{"method": "card", "amount": "8.99"}]})
        db.session.commit()
        customer = db.session.get(Customer, customer_id)
        assert customer.loyalty_points < 17  # roughly half clawed back
