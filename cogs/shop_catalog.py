from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, Dict, List, Any


@dataclass
class ShopItem:
    """
    Defines a catalog item in Sifdine's Shop and Inventory system.
    """
    id: str                         # Unique slug identifier, e.g. "vip_pass", "streak_shield"
    name: str                       # Display name, e.g. "Daily Streak Shield"
    emoji: str                      # Custom Discord emoji or unicode fallback, e.g. "🛡️"
    description: str                # Lore and mechanical explanation
    price: int                      # Cost in TAD
    category: str = "general"       # "perks", "cosmetics", "consumables", "collectibles"

    # Mechanics & Flags
    tradeable: bool = False         # False = Account-bound; True = Player-tradeable
    stackable: bool = True          # Can the user hold multiple units?
    max_stack: int = 99             # Maximum quantity an individual player can hold
    min_level: int = 1              # Minimum leveling level required to purchase
    usable: bool = False            # Can be consumed/activated via `sat use <id>`
    is_active_in_shop: bool = True  # Visible in the public shop catalog

    # Lifecycle Callbacks
    # on_buy(bot, user, ctx) -> (success: bool, message: str)
    on_buy: Optional[Callable[..., Awaitable[tuple[bool, str]]]] = None
    # on_use(bot, user, ctx) -> (success: bool, message: str)
    on_use: Optional[Callable[..., Awaitable[tuple[bool, str]]]] = None


# The global item registry. Add items here in the future to immediately make them live!
CATALOG: Dict[str, ShopItem] = {}


def register_item(item: ShopItem) -> ShopItem:
    """Registers an item into the global shop catalog."""
    CATALOG[item.id.lower()] = item
    return item


def get_item(item_id: str) -> Optional[ShopItem]:
    """Retrieves a registered item by its slug ID."""
    return CATALOG.get(item_id.lower().strip()) if item_id else None


def get_active_shop_items() -> List[ShopItem]:
    """Returns all items currently available for purchase in the static shop."""
    return [item for item in CATALOG.values() if item.is_active_in_shop]


def get_items_by_category(category: str) -> List[ShopItem]:
    """Filters active shop items by their category."""
    cat = category.lower().strip()
    return [item for item in CATALOG.values() if item.is_active_in_shop and item.category.lower() == cat]


async def setup(bot):
    pass