from drf_spectacular.utils import extend_schema
from rest_framework.views import APIView
from rest_framework.response import Response

from anthias_app.models import Asset
from api.serializers.v2 import AssetSerializerV2
from lib.auth import authorized


class AssetListViewV2(APIView):
    serializer_class = AssetSerializerV2

    @extend_schema(
        summary='List assets',
        responses={
            200: AssetSerializerV2(many=True)
        }
    )
    @authorized
    def get(self, request):
        queryset = Asset.objects.all()
        serializer = AssetSerializerV2(queryset, many=True)
        return Response(serializer.data)
