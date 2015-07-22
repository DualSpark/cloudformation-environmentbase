from unittest2 import TestCase, main
import mock
from mock import patch
import os
import shutil
import sys
import json
from tempfile import mkdtemp
from environmentbase import cli, template
import troposphere as tropo
from troposphere import ec2

# commentjson is optional, parsing invalid json throws commonjson.JSONLibraryException
# if not present parsing invalid json throws __builtin__.ValueError.
# Make them the same and don't worry about it
try:
    from commentjson import JSONLibraryException as ValueError
except ImportError:
    pass


class TemplateTestCase(TestCase):

    def setUp(self):
        # Change to a temp dir so auto generated file don't clutter the os
        self.temp_dir = mkdtemp()
        os.chdir(self.temp_dir)

    def tearDown(self):
        # Delete any files left in the temp dir
        shutil.rmtree(self.temp_dir)
        assert not os.path.isdir(self.temp_dir)

    def fake_cli(self, extra_args):
        args = ['environmentbase']
        args.extend(extra_args)

        with patch.object(sys, 'argv', args):
            my_cli = cli.CLI(quiet=True)
            my_cli.process_request = mock.MagicMock()

        return my_cli

    def _create_local_file(self, name, content):
        f = open(os.path.join(self.temp_dir, name), 'a')
        f.write(content)
        f.flush()
        return f

    def test_tropo_to_string(self):
        template.tropo_to_string(tropo.Template())
        template.tropo_to_string(tropo.Base64('efsdfsdf'))
        template.tropo_to_string(tropo.Output('efsdfsdf', Value='dsfsdfs'))
        template.tropo_to_string(tropo.Parameter('efsdfsdf', Type='dsfsdfs'))

        # These constructors recursively call themselves for some reason
        # Don't instantiate directly
        # template.tropo_to_string(tropo.AWSProperty())
        # template.tropo_to_string(tropo.AWSAttribute())

        template.tropo_to_string(ec2.Instance(
            "ec2instance",
            InstanceType="m3.medium",
            ImageId="ami-951945d0"))

if __name__ == '__main__':
    main()
