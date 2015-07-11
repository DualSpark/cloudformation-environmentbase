from __future__ import print_function
import unittest
import mock
import os

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

    def test_config_override(self):
        # Create a fake temp file
        from tempfile import NamedTemporaryFile
        temp = NamedTemporaryFile()

        # Add config_file override flag
        self.view.args['--config_file'] = temp.name
        # ------------------

        # bad json test
        with self.assertRaises(ValueError):
            EnvironmentBase(self.view)
        # -----------------

        # Add a minimal json structure to avoid a parsing exception
        print('{}', file=temp.file)
        temp.flush()

        # good json test
        EnvironmentBase(self.view)
        # ------------------

        # Temp files auto-delete on close, let's verify that
        temp.close()
        assert not os.path.isfile(temp.name)

        # no file test
        with self.assertRaises(IOError):
            EnvironmentBase(self.view)


if __name__ == '__main__':
    unittest.main()
