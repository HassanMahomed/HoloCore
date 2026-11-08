"""Compatibility imports for the former internal Memories module.

New HoloCore code should import :mod:`holocore.memories` and use
``MemoryStore``. These aliases remain throughout v1 for existing integrations.
"""

from .memories import *  # noqa: F401,F403
from .memories import __all__
