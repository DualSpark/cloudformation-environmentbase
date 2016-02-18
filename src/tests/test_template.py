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
from environmentbase.template import Template, TemplateValueError
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

    def test_get_instancetype_param(self):
        Template.instancetype_to_arch = {"t2.nano": "HVM64"}

        t = Template('test')

        # Verify validation of instance types from config
        with self.assertRaises(TemplateValueError):
            t.get_instancetype_param('t2.pico', 'TestLayer')

        with self.assertRaises(TemplateValueError):
            t.get_instancetype_param(1234, 'TestLayer')

        # Verify created parameter is correct
        param = t.get_instancetype_param('t2.nano',
                                         'TestLayer',
                                         allowed_instance_types=["t2.nano", "test_type.medium"])

        param_name = Template.instancetype_param_name('TestLayer')

        self.assertIn(param_name, t.parameters)
        self.assertEqual(param, t.parameters[param_name])
        self.assertIn("test_type.medium", param.properties['AllowedValues'])

    def test_get_ami_expr(self):
        Template.instancetype_to_arch = {"t2.nano": "HVM64"}
        Template.image_map = {
            "testImage": {
                "us-west-2": {"HVM64": "ami-e7527ed7", "PV64": "ami-ff527ecf"}
            }
        }

        t = Template('test')

        with self.assertRaises(KeyError):
            t.get_ami_expr('t2.nano', 'missingImage', 'TestLayer1')

        ami_expr = t.get_ami_expr('t2.nano',
                                  'testImage',
                                  'TestLayer2',
                                  allowed_instance_types=["t2.nano", "test_type.large"])

        # verify instancetype parameter and its allowed_instance_types
        instancetype_param = t.parameters[Template.instancetype_param_name('TestLayer2')]
        self.assertIn("test_type.large", instancetype_param.properties['AllowedValues'])

        # verify template containts new Mappings
        image_map_name = Template.image_map_name('testImage')
        self.assertIn(image_map_name, t.mappings)
        self.assertIn(Template.ARCH_MAP, t.mappings)

        # Check the expression
        self.assertTrue(isinstance(ami_expr, tropo.FindInMap))  # check expr type

        # verify primary key is the region
        expr_map = ami_expr.data['Fn::FindInMap']
        self.assertEqual(expr_map[0], image_map_name)
        self.assertEqual(expr_map[1].data['Ref'], 'AWS::Region')

        # verify secondary key is nested FindInMap on architecture map with primary key
        # as the value of the instance-type parameter and secondary key as 'Arch'
        inner_map = expr_map[2].data['Fn::FindInMap']
        self.assertTrue(inner_map[0], Template.ARCH_MAP)
        self.assertTrue(inner_map[1].data['Ref'], instancetype_param.title)
        self.assertTrue(inner_map[2], 'Arch')

if __name__ == '__main__':
    main()
