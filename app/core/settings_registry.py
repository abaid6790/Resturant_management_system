"""
Every configurable business rule is declared here (default, type, limits). Later phases append
their own (tax rates, service charge, low-stock thresholds ...). Nothing is hardcoded elsewhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo, available_timezones


@dataclass(frozen=True)
class SettingDef:
    key: str
    group: str
    label: str
    kind: str  # str | text | int | bool | choice | timezone | currency
    default: object
    help: str = ""
    choices: tuple = ()
    min: int | None = None
    max: int | None = None
    required: bool = False


REGISTRY: list[SettingDef] = [
    SettingDef("restaurant.name", "Restaurant", "Restaurant name", "str", "My Restaurant",
               required=True),
    SettingDef("restaurant.legal_name", "Restaurant", "Legal name", "str", ""),
    SettingDef("restaurant.address", "Restaurant", "Address", "text", ""),
    SettingDef("restaurant.phone", "Restaurant", "Phone", "str", ""),
    SettingDef("restaurant.email", "Restaurant", "Email", "str", ""),
    SettingDef("restaurant.tax_id", "Restaurant", "Tax registration number", "str", ""),
    SettingDef("locale.timezone", "Regional", "Time zone", "timezone", "UTC", required=True,
               help="Used to display dates and times, and to decide when a business day ends."),
    SettingDef("currency.code", "Regional", "Currency code", "currency", "USD", required=True,
               help="Three letters, e.g. USD, EUR, PKR."),
    SettingDef("currency.symbol", "Regional", "Currency symbol", "str", "$", required=True),
    SettingDef("currency.symbol_position", "Regional", "Symbol position", "choice", "before",
               choices=(("before", "Before the amount"), ("after", "After the amount"))),
    SettingDef("receipt.header", "Receipts", "Receipt header", "text", "",
               help="Printed at the top of receipts and invoices."),
    SettingDef("receipt.footer", "Receipts", "Receipt footer", "text", "Thank you for dining with us.",
               help="Printed at the bottom of receipts and invoices."),
    SettingDef("receipt.show_tax_id", "Receipts", "Show tax registration number", "bool", True),
    SettingDef("security.session_idle_minutes", "Security", "Sign out after inactivity (minutes)",
               "int", 480, min=5, max=1440),
    SettingDef("security.max_failed_logins", "Security", "Failed sign-ins before lockout", "int", 5,
               min=3, max=20),
    SettingDef("security.lockout_minutes", "Security", "Lockout duration (minutes)", "int", 15,
               min=1, max=1440),
    SettingDef("security.password_min_length", "Security", "Minimum password length", "int", 8,
               min=6, max=64),
    SettingDef("inventory.low_stock_warning_pct", "Inventory", "Low-stock warning threshold (%)",
               "int", 120, min=100, max=300,
               help="Flag an item as running low once stock falls below this % of its reorder level."),
    SettingDef("inventory.expiry_warning_days", "Inventory", "Expiry warning (days)", "int", 7,
               min=1, max=90, help="Flag a batch as expiring soon this many days before its expiry date."),
    SettingDef("inventory.allow_negative_stock", "Inventory", "Allow stock to go negative", "bool",
               False, help="Off is recommended: adjustments and transfers are blocked once stock hits zero."),
    SettingDef("purchasing.require_approval", "Purchasing", "Require approval before receiving",
               "bool", False,
               help="On: a purchase order must be approved (separately from whoever created it) "
                    "before it can be received. Off: submitting a purchase order approves it "
                    "immediately."),
    SettingDef("purchasing.default_payment_terms_days", "Purchasing", "Default payment terms (days)",
               "int", 0, min=0, max=365,
               help="Used as the default due date on new invoices for suppliers with no terms set."),
    SettingDef("purchasing.po_number_prefix", "Purchasing", "Purchase order number prefix", "str",
               "PO", help="e.g. PO-000123."),
]
BY_KEY: dict[str, SettingDef] = {d.key: d for d in REGISTRY}


def coerce(defn: SettingDef, raw) -> object:
    """Validate a raw form value and return it in its stored type. Raises ValueError(message)."""
    if defn.kind == "bool":
        return raw in (True, "on", "true", "1", "yes")
    text = (raw or "").strip() if isinstance(raw, str) or raw is None else str(raw)
    if defn.required and not text:
        raise ValueError("This field is required.")
    if defn.kind in ("str", "text"):
        if len(text) > (2000 if defn.kind == "text" else 255):
            raise ValueError("That is too long.")
        return text
    if defn.kind == "int":
        try:
            n = int(text)
        except ValueError as exc:
            raise ValueError("Enter a whole number.") from exc
        if defn.min is not None and n < defn.min or defn.max is not None and n > defn.max:
            raise ValueError(f"Enter a number between {defn.min} and {defn.max}.")
        return n
    if defn.kind == "choice":
        if text not in dict(defn.choices):
            raise ValueError("Choose one of the listed options.")
        return text
    if defn.kind == "timezone":
        if text not in available_timezones():
            raise ValueError("Unknown time zone. Use a name like Asia/Karachi or Europe/London.")
        ZoneInfo(text)
        return text
    if defn.kind == "currency":
        text = text.upper()
        if len(text) != 3 or not text.isalpha():
            raise ValueError("Use a three-letter currency code, e.g. USD.")
        return text
    raise ValueError(f"Unsupported setting type {defn.kind}")
