"""Auth plugin's local Entity / Action / Event bases. The plugin alias
'auth' is declared exactly once, here."""

from hearth import bases_for

Entity, Action, Event = bases_for("auth")
