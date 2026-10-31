"""Error types with stable CLI exit codes."""


class CliError(Exception):
    exit_code = 1


class UsageError(CliError):
    exit_code = 2


class NoMatchesError(CliError):
    exit_code = 3


class GuardError(CliError):
    exit_code = 4


class RemoteError(CliError):
    exit_code = 5
