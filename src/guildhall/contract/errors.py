"""Typed failures a job handler may raise."""


class RetryableError(Exception):
    """A transient failure, such as a timeout or an upstream 429.

    The platform runs the job again with backoff; repeated failures move it to
    the dead-letter queue.
    """


class PermanentError(Exception):
    """A failure that will never succeed on retry.

    The platform moves the job straight to the dead-letter queue, recording the
    error message as the reason.
    """
