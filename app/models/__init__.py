"""Import every model module here so Alembic autogenerate sees all tables."""
from .audit import AuditLog  # noqa: F401
from .auth import Branch, Permission, Role, User, UserSession  # noqa: F401
from .inventory import (  # noqa: F401
    InventoryCategory,
    InventoryItem,
    ItemPackagingUnit,
    Location,
    Recipe,
    RecipeLine,
    StockBalance,
    StockBatch,
    StockMovement,
    UnitOfMeasure,
)
from .purchasing import (  # noqa: F401
    PurchaseInvoice,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseReceipt,
    PurchaseReceiptLine,
    PurchaseReturn,
    PurchaseReturnLine,
    Supplier,
    SupplierBalance,
    SupplierItem,
    SupplierLedgerEntry,
    SupplierPayment,
)
from .settings import Setting  # noqa: F401
from .system import SchemaMeta  # noqa: F401
