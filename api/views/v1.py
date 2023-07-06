from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from api.serializers import AssetListSerializer


class AssetListViewV1(APIView):
    serializer_class = AssetListSerializer

    def get(self, request, format=None):
        data = {
            'field1': 'value1',
            'field2': 'value2',
        }
        serializer = self.serializer_class(data=data)

        if serializer.is_valid():
            return Response(serializer.data)

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )
