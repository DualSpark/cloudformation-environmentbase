from unittest2 import TestCase
from environmentbase.resources import Res
from environmentbase.template import Template
from tempfile import mkdtemp
import shutil
import os
import json
import yaml


class ResourcesTestCase(TestCase):

    def setUp(self):
        Res._INCLUDE_RESOURCE_MODULE = 'environmentbase.resources'
        Res._INCLUDE_RESOURCE_INTERNAL_PATH = 'data'

        self.r = Res()

        # Change to a temp dir so auto generated file don't clutter the os
        self.temp_dir = mkdtemp()
        os.chdir(self.temp_dir)

    def tearDown(self):
        # Delete any files left in the temp dir
        shutil.rmtree(self.temp_dir)
        assert not os.path.isdir(self.temp_dir)

    def test_load_resource(self):
        # validate load from usual location
        content = self.r.load_resource('config.json')
        self.assertIn('global', content)

        # validate load from custom location
        content = self.r.load_resource('amzn_linux_ec2.json', module=__name__, internal_path='resources')
        self.assertIn("Sample Template", content)

        # Validate failure scenerios
        with self.assertRaises(Exception):
            self.r.load_resource('fake_file')

        with self.assertRaises(Exception):
            self.r.load_resource('config.json', resource_dir='wrong_path')

        with self.assertRaises(Exception):
            self.r.load_resource('config.json', module="wrong_module")

    def test_parse_file(self):

        # Configure resource loading to read from the test module
        Res._INCLUDE_RESOURCE_MODULE = __name__
        Res._INCLUDE_RESOURCE_INTERNAL_PATH = 'resources'

        # Verify resource loading:
        parsed_content = self.r.parse_file('amzn_linux_ec2.json', from_file=False)
        self.assertIn("Sample Template", parsed_content['Description'])

        # Save this file to the current directory (the temp dir for this test run)
        # with a modified description for verification
        parsed_content['Description'] = 'Blaaa'
        with open('amzn_linux_ec2.json', 'w') as f:
            f.write(json.dumps(parsed_content, indent=4, separators=(',', ': ')))

        # Verify file loading
        parsed_content = self.r.parse_file('amzn_linux_ec2.json', from_file=True)
        self.assertEquals("Blaaa", parsed_content['Description'])

    def test_extract_config_section(self):
        # Create bogus config object
        config = {
            'a': 'don\'t extract',
            'b': {'map': 'extract', 'test': range(1, 10)}
        }

        # verify extraction of complex structure
        self.r._extract_config_section(config, 'b', 'b.json')
        self.assertEquals(config['b'], '!include b.json')
        with file('b.json') as f:
            content = f.read()
            parsed_content = yaml.load(content)
            self.assertEquals(parsed_content, {'map': 'extract', 'test': range(1, 10)})

        # verify unextracted section was not changed
        self.assertEquals(config['a'], 'don\'t extract')

    def test_generate_config(self):
        # Configure resource loading to read from the test module
        Res._INCLUDE_RESOURCE_MODULE = __name__
        Res._INCLUDE_RESOURCE_INTERNAL_PATH = 'resources'

        custom_config_addition = {
            'custom': {
                'a': 'don\'t extract',
                'b': {'map': 'extract', 'test': range(1, 10)}
            },
            "AWSTemplateFormatVersion": "2016-02-05"
        }

        custom_config_validation = {
            'custom': {
                'a': 'basestring',
                'b': {'map': 'basestring', 'test': 'list'}
            }
        }

        class CustomHandler(Template):
            @staticmethod
            def get_factory_defaults():
                return custom_config_addition

            @staticmethod
            def get_config_schema():
                return custom_config_validation

        # Treat sample cfn template as if it were a config file for testing purposes
        self.r.generate_config(
            is_silent=True,
            config_file='amzn_linux_ec2.json',
            extract_map={
                "Description": "description.json",
                "Parameters": "params.json",
                "Mappings": "mappings.json",
                "Resources": "resources.json",
                "Outputs": "output.json"
            }
        )

        # Make sure all the extracted files exist
        with file('amzn_linux_ec2.json') as f:
            content = f.read()
            for inc_file in ['description.json', 'params.json', 'mappings.json', 'resources.json', 'output.json']:
                self.assertIn('!include %s' % inc_file, content)

        # Read the JSON into Python data structure
        parsed_content = self.r.parse_file('amzn_linux_ec2.json', from_file=True)

        # Verify you got a datastructure back
        self.assertTrue(isinstance(parsed_content, dict))

        # Verify new config section is added
        self.assertEquals(parsed_content['custom'], custom_config_addition['custom'])

        # Verify modified config section is modified
        self.assertEquals(parsed_content['AWSTemplateFormatVersion'], "2016-02-05")
