"""Query errors with human-readable messages (spec 012)."""


class QueryError(ValueError):
    """A malformed query whose message is meant to be shown to a person.

    Raised only by parsing/validation. The CLI prints ``error: <msg>`` on
    stderr and the GUI service catches it into ``last_query_error`` and
    returns no results — an invalid query is feedback, never a traceback
    and never a crash of the CLI, GUI or background processes.
    """
