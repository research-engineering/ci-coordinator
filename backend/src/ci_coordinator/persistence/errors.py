import asyncio


class PersistenceError(RuntimeError):
    pass


class StoreUnavailable(PersistenceError):
    pass


class CommitOutcomeUnknown(PersistenceError):
    pass


class CommitCancelledOutcomeUnknown(asyncio.CancelledError):
    pass


class CommittedButCleanupFailed(PersistenceError):
    pass


class MigrationMismatch(PersistenceError):
    pass


class PersistenceInvariantViolation(PersistenceError):
    pass


class DatabaseCompatibilityError(PersistenceError):
    """The database cannot safely admit a schema-dependent operation."""


class DatabaseCapabilityUnavailable(DatabaseCompatibilityError):
    pass


class DatabaseCompatibilityTimeout(DatabaseCompatibilityError):
    pass


class DatabaseQueryCancelled(DatabaseCompatibilityError):
    pass


class DatabaseCompatibilityTransactionTimeout(DatabaseCompatibilityError):
    pass


class DatabaseCompatibilityOutcomeUnknown(DatabaseCompatibilityError):
    pass
