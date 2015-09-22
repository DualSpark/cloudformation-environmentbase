from unittest2 import TestCase, main
import mock
from mock import patch
import os
import shutil
import sys
from tempfile import mkdtemp
from environmentbase import cli, template, utility
import troposphere as tropo
from troposphere import ec2
import yaml


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
        utility.tropo_to_string(tropo.Template())
        utility.tropo_to_string(tropo.Base64('efsdfsdf'))
        utility.tropo_to_string(tropo.Output('efsdfsdf', Value='dsfsdfs'))
        utility.tropo_to_string(tropo.Parameter('efsdfsdf', Type='dsfsdfs'))

        # These constructors recursively call themselves for some reason
        # Don't instantiate directly
        # utility.tropo_to_string(tropo.AWSProperty())
        # utility.tropo_to_string(tropo.AWSAttribute())

        utility.tropo_to_string(ec2.Instance(
            "ec2instance",
            InstanceType="m3.medium",
            ImageId="ami-951945d0"))

    def test_build_bootstrap(self):
        file1_name = 'arbitrary_file.txt'
        file1_content = 'line1\nline2\nline3'
        self._create_local_file(file1_name, file1_content)

        # Basic test
        template_snippet = template.Template.build_bootstrap([file1_name], prepend_line='')
        generated_json = yaml.load(utility.tropo_to_string(template_snippet))
        expected_json_1 = {"Fn::Base64": {"Fn::Join": ["\n", ["line1", "line2", "line3"]]}}
        self.assertEqual(generated_json, expected_json_1)

        # Advanced test

        # resources can't be accessed as files directly
        # lines starting with #~ are removed automatically
        file2_content = '#~this_line_should_be_stripped_out\nline4\nline5\nline6'

        # you can provided multiple files or content (mix and match) in the specified order
        # you can set the shabang to whatever you want
        # you can reference variables in the file content and set there values using variable_declarations
        # finally to can do any append cleanup commands to the bottom of the file with cleanup_commands
        template_snippet = template.Template.build_bootstrap(
            [file1_name, file2_content],
            prepend_line='#!/bin/fakesh',
            variable_declarations=['var_dec_line_1', 'var_dec_line_2'],
            cleanup_commands=['cleanup_line_1', 'cleanup_line_2'])

        generated_json = yaml.load(utility.tropo_to_string(template_snippet))
        expected_json_2 = {
            "Fn::Base64": {"Fn::Join": [
                "\n", [
                    "#!/bin/fakesh",
                    'var_dec_line_1', 'var_dec_line_2',
                    "line1", "line2", "line3",
                    "line4", "line5", "line6",
                    'cleanup_line_1', 'cleanup_line_2'
                ]
            ]}}

        self.assertEqual(generated_json, expected_json_2)

if __name__ == '__main__':
    main()
