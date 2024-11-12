from rest_framework.views import APIView
from rest_framework.response import Response

from anthias_app.models import Asset
from api.serializers import AssetSerializerV2


class AssetListViewV2(APIView):
    def get(self, request):
        queryset = Asset.objects.all()
        serializer = AssetSerializerV2(queryset, many=True)
        return Response(serializer.data)
