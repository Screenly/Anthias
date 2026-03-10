from api.urls.v2 import get_url_patterns as get_url_patterns_v2

app_name = 'api'

urlpatterns = [
    *get_url_patterns_v2(),
]
