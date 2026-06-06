"""Migration-system error hierarchy."""
# ruff: noqa: N818 -- exception class names are part of the migration-system
# public contract and intentionally describe the condition without an `Error`
# suffix; renaming would break references throughout the migrations module.

from hearth.errors import HearthError


class MigrationError(HearthError):
    """Base for all migration-system errors."""


class MissingClassificationArtifact(MigrationError):
    """A revision file lacks `HEARTH_UPGRADE_OPS`; cannot determine safety."""


class MigrationLockContention(MigrationError):
    """Another migration is in progress (advisory lock held)."""


class PartialCommitUnrecoverable(MigrationError):
    """An orphaned revision exists and recovery failed."""


class PluginDowngradeDetected(MigrationError):
    """Applied head is ahead of what the plugin ships; rollback (not v1) needed."""


class AdoptionDriftDetected(MigrationError):
    """Existing install has tables but live schema does not match declared schema."""
