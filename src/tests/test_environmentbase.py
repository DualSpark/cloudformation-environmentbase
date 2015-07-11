import unittest
import mock
import sys
from mock import patch

from environmentbase import EnvironmentBase

# view.args = {
#     '--config_file': False,
#     '--debug': False,
#     '--generate_topics': False,
#     '--no_tests': True,
#     '--region': 'all',
#     '--third_party_auth_ids': False,
#     '--topic_name': None,
#     '--trail_name': 'Default',
#     '<FILE_LOCATION>': None,
#     '<action>': 'create'
# }


class EnvironmentBaseTestCase(unittest.TestCase):
    def setUp(self):
        self.view = mock.MagicMock()
        self.view.process_request = mock.MagicMock()

        # We wouldn't want to recursively run this test suite
        self.view.args = {'--no_tests': True}

    def test_constructor(self):
        env_base = EnvironmentBase(self.view)

        # Check that EnvironmentBase started the CLI
        self.view.process_request.assert_called_once_with(env_base)

if __name__ == '__main__':
    unittest.main()
