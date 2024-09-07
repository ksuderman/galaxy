import yaml

from ._framework import ApiTestCase


class TestLoggingApi(ApiTestCase):
    def test_index(self):
        response = self._get("logging")
        # self._assert_status_code_is(response, 200)
        print(yaml.dump(response))