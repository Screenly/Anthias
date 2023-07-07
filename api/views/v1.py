from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers import AssetListSerializer
from lib import (
    assets_helper,
    db,
)
from settings import settings


class AssetListViewV1(APIView):
    serializer_class = AssetListSerializer

    def get(self, request, format=None):
        with db.conn(settings['database']) as conn:
            data = assets_helper.read(conn)

        serializer = self.serializer_class(data=data, many=True)

        if serializer.is_valid():
            return Response(serializer.data)

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )
