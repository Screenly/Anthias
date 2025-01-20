from api.urls.v1 import get_url_patterns as get_url_patterns_v1
from api.urls.v1_1 import get_url_patterns as get_url_patterns_v1_1
from api.urls.v1_2 import get_url_patterns as get_url_patterns_v1_2
from api.urls.v2 import get_url_patterns as get_url_patterns_v2

app_name = 'api'

urlpatterns = [
    *get_url_patterns_v1(),
    *get_url_patterns_v1_1(),
    *get_url_patterns_v1_2(),
    *get_url_patterns_v2(),
]
