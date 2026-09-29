"""Game Card format v0: shared validation for host tools and the card service.

The same code inspects a card image offline on the host (through `debugfs`)
and validates a mounted card inside the console (through the filesystem), so
both sides agree on what a valid card is. See docs/game-cards.md.
"""

from .errors import CardError  # noqa: F401
from .validate import CardInfo, validate_card  # noqa: F401

__version__ = "0.1.0"
