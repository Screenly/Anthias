from redis.backoff import AbstractBackoff

class Retry:
    def __init__(self, backoff: AbstractBackoff, retries: int) -> None: ...
