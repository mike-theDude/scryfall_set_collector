"""Expand owned sets into card-level collection entries. Implemented in issue #7.

Generated entries use source_type='full_set' with source_set_code=<set>, so that
set removal (issue #9) can target only generated rows and never touch manual
singles or overrides. See docs/DESIGN.md.
"""

from __future__ import annotations
