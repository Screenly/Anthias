"""drf-spectacular extensions for Anthias' custom authenticators."""

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.openapi import AutoSchema


class BearerTokenScheme(OpenApiAuthenticationExtension):
    target_class = 'anthias_server.lib.auth.BearerTokenAuthentication'
    name = 'bearerAuth'

    def get_security_definition(
        self, auto_schema: AutoSchema
    ) -> dict[str, Any]:
        return {
            'type': 'http',
            'scheme': 'bearer',
            'description': (
                'API token created under Settings → API tokens. Only '
                'enforced while authentication is enabled.'
            ),
        }
