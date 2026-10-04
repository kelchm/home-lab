"""Errors the archive reports. Each maps to one CLI exit code."""


class ArchiveError(Exception):
    """An operation failed."""

    exit_code = 1


class IntegrityError(ArchiveError):
    """Bytes did not match the identity upstream metadata declares for them."""

    exit_code = 3


class ScopeError(ArchiveError):
    """The repository is outside what this archive is allowed to hold."""

    exit_code = 4


class NoSpaceError(ArchiveError):
    """The archive filesystem cannot hold the requested content."""

    exit_code = 5


class InvalidDocument(ArchiveError):
    """A persisted or supplied JSON document is malformed or unsafe."""
