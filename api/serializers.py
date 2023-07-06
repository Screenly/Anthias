from rest_framework.serializers import (
    CharField,
    Serializer,
)

class AssetListSerializer(Serializer):
    # @TODO: Remove these dummy fields.
    field1 = CharField()
    field2 = CharField()
