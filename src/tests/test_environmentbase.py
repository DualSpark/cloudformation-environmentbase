from __future__ import print_function
from unittest2 import TestCase, main
import mock
import os
import shutil
import json
import copy
from tempfile import NamedTemporaryFile, mkdtemp
from environmentbase.environmentbase import *


class EnvironmentBaseTestCase(TestCase):
    def setUp(self):
        self.view = mock.MagicMock()
        self.view.process_request = mock.MagicMock()
        self.view.args = {'create': True}

        # Create fake config string
        self.dummy_value = 'dummy'
        self.valid_config = {}
        for (section, keys) in TEMPLATE_REQUIREMENTS.iteritems():
            self.valid_config[section] = {}
            for key in keys:
                self.valid_config[section][key] = self.dummy_value

        # Change to a temp dir so auto generated file don't clutter the os
        self.temp_dir = mkdtemp()
        os.chdir(self.temp_dir)

    def tearDown(self):
        assert os.path.isdir(self.temp_dir)
        shutil.rmtree(self.temp_dir)
        assert not os.path.isdir(self.temp_dir)

    def test_constructor(self):
        env_base = EnvironmentBase(self.view)

        # Check that EnvironmentBase started the CLI
        self.view.process_request.assert_called_once_with(env_base)

    def test_config_override(self):

        # Create fake ami_cache (we aren't testing this file but we need it since create_missing_files will be disabled)
        ami_cache = open(os.path.join(self.temp_dir, DEFAULT_AMI_CACHE_FILENAME), 'a')
        ami_cache.write("{}")
        ami_cache.flush()

        # Create a fake config file
        temp = NamedTemporaryFile()

        # Add config_file override flag
        self.view.args['--config_file'] = temp.name
        # ------------------

        # bad json test
        with self.assertRaises(ValueError):
            EnvironmentBase(self.view, create_missing_files=False)
        # -----------------

        # Add a minimal json structure to avoid a parsing exception
        print(json.dumps(self.valid_config), file=temp.file)
        temp.flush()

        # good json test
        EnvironmentBase(self.view, create_missing_files=False)
        # ------------------

        # Temp files auto-delete on close, let's verify that
        temp.close()
        assert not os.path.isfile(temp.name)

        # no file test
        with self.assertRaises(IOError):
            EnvironmentBase(self.view, create_missing_files=False)

    def test_flags(self):
        # Add config_file override flag
        temp = NamedTemporaryFile()
        print(json.dumps(self.valid_config), file=temp.file)
        temp.flush()
        self.view.args['--config_file'] = temp.name

        # test the defaults
        eb = EnvironmentBase(self.view)
        self.assertFalse(eb.debug)
        self.assertEqual(eb.stack_name, self.dummy_value)
        self.assertEqual(eb.template_filename, self.dummy_value)

        # override tests
        # - config cli flag
        # - recreate EB
        # - test result
        # - remove cli flag

        self.view.args['--debug'] = True
        eb = EnvironmentBase(self.view)
        self.assertTrue(eb.debug)
        del self.view.args['--debug']

        self.view.args['--stack_name'] = 'stack_name_override'
        eb = EnvironmentBase(self.view)
        self.assertEqual(eb.stack_name, 'stack_name_override')
        del self.view.args['--stack_name']

        self.view.args['--template_file'] = 'template_file_override'
        eb = EnvironmentBase(self.view)
        self.assertEqual(eb.template_filename, 'template_file_override')
        del self.view.args['--template_file']

        temp.close()

    def test_config_validation(self):
        EnvironmentBase._validate_config(self.valid_config)

        config_copy = copy.deepcopy(self.valid_config)

        # Find a section with at least one required key
        section = ''
        keys = {}
        while True:
            (section, keys) = config_copy.items()[0]
            if len(keys) > 0:
                break
        assert len(keys) > 0

        # Check missing key validation
        (key, value) = keys.items()[0]
        del config_copy[section][key]

        with self.assertRaises(ValidationError):
            EnvironmentBase._validate_config(config_copy)

        # Check missing section validation
        del config_copy[section]

        with self.assertRaises(ValidationError):
            EnvironmentBase._validate_config(config_copy)

    def test_factory_default(self):
        # print ('test_factory_default::view.args', self.view.args)

        with self.assertRaises(IOError):
            EnvironmentBase(self.view, create_missing_files=False)

        # Create refs to files that should be created and make sure they don't already exists
        config_file = os.path.join(self.temp_dir, DEFAULT_CONFIG_FILENAME)
        ami_cache_file = os.path.join(self.temp_dir, DEFAULT_AMI_CACHE_FILENAME)
        self.assertFalse(os.path.isfile(config_file))
        self.assertFalse(os.path.isfile(ami_cache_file))

        # Verify that create_missing_files works as intended
        EnvironmentBase(self.view, create_missing_files=True)
        self.assertTrue(os.path.isfile(config_file))
        self.assertTrue(os.path.isfile(ami_cache_file))

        # Verify that the previously created files are loaded up correctly
        EnvironmentBase(self.view, create_missing_files=False)


if __name__ == '__main__':
    main()
