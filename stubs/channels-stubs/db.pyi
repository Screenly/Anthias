from collections.abc import Awaitable, Callable

def database_sync_to_async[**P, R](
    func: Callable[P, R],
) -> Callable[P, Awaitable[R]]: ...
