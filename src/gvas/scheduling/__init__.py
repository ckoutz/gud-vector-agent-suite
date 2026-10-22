"""The scheduling engine: open times, holds, visits and blocked time.

``core`` knows calendars, resources, availability and events and nothing
about any line of work. Packs (``field_service`` today) add their own tables
and link to the rest of GVAS by id only.
"""
