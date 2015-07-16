from unittest2 import TestCase, main
import mock
from mock import patch
import os
import shutil
import json
import sys
from tempfile import NamedTemporaryFile, mkdtemp
from environmentbase import cli, environmentbase as eb
from troposphere import ec2


class EnvironmentBaseTestCase(TestCase):
    def setUp(self):
        self.view = mock.MagicMock()
        self.view.process_request = mock.MagicMock()

        # Change to a temp dir so auto generated file don't clutter the os
        self.temp_dir = mkdtemp()
        os.chdir(self.temp_dir)

    def tearDown(self):
        # Delete any files left in the temp dir
        shutil.rmtree(self.temp_dir)
        assert not os.path.isdir(self.temp_dir)

    def _create_dummy_config(self, dummy_value):
        config = {}
        for (section, keys) in eb.TEMPLATE_REQUIREMENTS.iteritems():
            config[section] = {}
            for key in keys:
                config[section][key] = dummy_value
        return config

    def _create_local_file(self, name, content):
        f = open(os.path.join(self.temp_dir, name), 'a')
        f.write(content)
        f.flush()
        return f

    def test_constructor(self):
        """Make sure EnvironmentBase passes control to view to process user requests"""
        self.view.args = {'create': True}
        env_base = eb.EnvironmentBase(self.view)

        # Check that EnvironmentBase started the CLI
        self.view.process_request.assert_called_once_with(env_base)

    def test_config_override(self):
        """ When providing an alternate config_file location verify that it's loading the right file."""

        # We don't care about the AMI cache, but we the file to exist and to contain valid json
        self._create_local_file(eb.DEFAULT_AMI_CACHE_FILENAME, '{}')
        self.view.args = {'create': True}

        # Create a config file -- not in local dir --
        temp = NamedTemporaryFile()

        # Add config_file override flag
        self.view.args['--config_file'] = temp.name

        # ------------------

        # bad json test
        with self.assertRaises(ValueError):
            eb.EnvironmentBase(self.view, create_missing_files=False)
        # -----------------

        # Add a minimal json structure to avoid a parsing exception
        valid_config = self._create_dummy_config('dummy')
        temp.write(json.dumps(valid_config))
        temp.flush()

        # good json test
        eb.EnvironmentBase(self.view, create_missing_files=False)
        # ------------------

        # Temp files auto-delete on close, let's verify that
        temp.close()
        assert not os.path.isfile(temp.name)

        # no file test
        with self.assertRaises(IOError):
            eb.EnvironmentBase(self.view, create_missing_files=False)

    def test_flags(self):
        dummy_value = 'dummy'
        valid_config = self._create_dummy_config(dummy_value)
        self.view.args = {'create': True}

        # Add config_file override flag
        temp = NamedTemporaryFile()
        temp.file.write(json.dumps(valid_config))
        temp.flush()
        self.view.args['--config_file'] = temp.name

        # test the defaults
        base = eb.EnvironmentBase(self.view)
        self.assertFalse(base.debug)
        self.assertEqual(base.stack_name, dummy_value)
        self.assertEqual(base.template_filename, dummy_value)

        # override tests
        # - config cli flag
        # - recreate EB
        # - test result
        # - remove cli flag

        self.view.args['--debug'] = True
        base = eb.EnvironmentBase(self.view)
        self.assertTrue(base.debug)
        del self.view.args['--debug']

        self.view.args['--stack_name'] = 'stack_name_override'
        base = eb.EnvironmentBase(self.view)
        self.assertEqual(base.stack_name, 'stack_name_override')
        del self.view.args['--stack_name']

        self.view.args['--template_file'] = 'template_file_override'
        base = eb.EnvironmentBase(self.view)
        self.assertEqual(base.template_filename, 'template_file_override')
        del self.view.args['--template_file']

        temp.close()

    def test_config_validation(self):
        valid_config = self._create_dummy_config('dummy')
        eb.EnvironmentBase._validate_config(valid_config)
        self.view.args = {'create': True}

        # config_copy = copy.deepcopy(valid_config)

        # Find a section with at least one required key
        section = ''
        keys = {}
        while True:
            (section, keys) = valid_config.items()[0]
            if len(keys) > 0:
                break
        assert len(keys) > 0

        # Check missing key validation
        (key, value) = keys.items()[0]
        del valid_config[section][key]

        with self.assertRaises(eb.ValidationError):
            eb.EnvironmentBase._validate_config(valid_config)

        # Check missing section validation
        del valid_config[section]

        with self.assertRaises(eb.ValidationError):
            eb.EnvironmentBase._validate_config(valid_config)

    def test_factory_default(self):
        self.view.args = {'create': True}

        with self.assertRaises(IOError):
            eb.EnvironmentBase(self.view, create_missing_files=False)

        # Create refs to files that should be created and make sure they don't already exists
        config_file = os.path.join(self.temp_dir, eb.DEFAULT_CONFIG_FILENAME)
        ami_cache_file = os.path.join(self.temp_dir, eb.DEFAULT_AMI_CACHE_FILENAME)
        self.assertFalse(os.path.isfile(config_file))
        self.assertFalse(os.path.isfile(ami_cache_file))

        # Verify that create_missing_files works as intended
        eb.EnvironmentBase(self.view, create_missing_files=True)
        self.assertTrue(os.path.isfile(config_file))
        self.assertTrue(os.path.isfile(ami_cache_file))

        # Verify that the previously created files are loaded up correctly
        eb.EnvironmentBase(self.view, create_missing_files=False)

    def test_controller_subclass(self):
        class MySubclass(eb.EnvironmentBase):
            def __init__(self, view):
                # Run parent initializer
                eb.EnvironmentBase.__init__(self, view)

            def create_action(self):
                # Add some stuff
                res = ec2.Instance("ec2instance", InstanceType="m3.medium", ImageId="ami-951945d0")
                self.template.add_resource(res)

                # This triggers serialization of the template and any child stacks
                super(MySubclass, self).create_action()

        # Initialize the the controller with faked 'create' CLI parameter
        with patch.object(sys, 'argv', ['environmentbase', 'create']):
            MySubclass(cli.CLI(quiet=True))

        # Load the generated output template
        with open('environmentbase.template', 'r') as f:
            template = json.load(f)

        # Verify that the ec2 instance is in the output
        self.assertTrue('ec2instance' in template['Resources'])

        # print json.dumps(template, indent=4)

if __name__ == '__main__':
    main()
