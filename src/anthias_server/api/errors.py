from typing import Any


class AssetCreationError(Exception):
    """An asset the create endpoints refuse; answered with a 400.

    The single definition: the serializers raise it and every create
    view, plus the content importer, catches it. There used to be a
    second class of the same name in ``api.helpers``, and the views
    caught that one, so anything the serializer raised slipped past
    them and the generic handler turned a client error into a 500.

    ``errors`` is the response body, so it is a mapping, as DRF's
    serializer errors are.
    """

    def __init__(self, errors: Any) -> None:
        super().__init__(errors)
        self.errors = errors
