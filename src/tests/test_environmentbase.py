from unittest2 import TestCase, main
import mock
from mock import patch
import os
import shutil
import json
import sys
from tempfile import mkdtemp
from environmentbase import cli, environmentbase as eb
from troposphere import ec2


class EnvironmentBaseTestCase(TestCase):

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
        fake_cli = self.fake_cli(['create'])
        env_base = eb.EnvironmentBase(fake_cli)

        # Check that EnvironmentBase started the CLI
        fake_cli.process_request.assert_called_once_with(env_base)

    def test_alternate_view(self):
        """ More of an example of how to use your own custom view than a test """
        actions_called = {'deploy': 0, 'create': 0}

        class MyView(object):

            def __init__(self):
                # Start an api, a web server or a rich client UI for example
                # Record user request(s), the controller will then call process_request()
                # so the can relay user requests to the appropriate controller action
                self.user_actions = ['create', 'deploy', 'deploy']
                self.user_config_changes = {'debug': True, 'output_filename': 'output.txt'}

            def update_config(self, config):
                # Update any config properties you need to
                config['global']['print_debug'] = self.user_config_changes['debug']
                config['global']['output'] = self.user_config_changes['output_filename']

            def process_request(self, controller):

                for action in self.user_actions:
                    # if action == 'create':
                    #     controller.create_action()
                    # elif action == 'deploy':
                    #     controller.deploy_action()

                    actions_called[action] += 1

        eb.EnvironmentBase(MyView())

        self.assertEqual(actions_called['create'], 1)
        self.assertEqual(actions_called['deploy'], 2)

    def test_config_override(self):
        """ Make sure local config files overrides default values."""

        # We don't care about the AMI cache for this test,
        # but the file has to exist and to contain valid json
        self._create_local_file(eb.DEFAULT_AMI_CACHE_FILENAME, '{}')

        fake_cli = self.fake_cli(['create'])

        # We don't use the factory_defualts as the real config so if no config file exists,
        # and we are asked not to create a new one then we must fail and exit
        assert not os.path.isfile(eb.DEFAULT_CONFIG_FILENAME)

        with self.assertRaises(IOError):
            eb.EnvironmentBase(fake_cli, create_missing_files=False)

        assert not os.path.isfile(eb.DEFAULT_CONFIG_FILENAME)

        # If the file exists but is not valid json we fail out
        with open(eb.DEFAULT_CONFIG_FILENAME, 'w') as f:
            f.write("{}")
            with self.assertRaises(ValueError):
                eb.EnvironmentBase(fake_cli, create_missing_files=False)

        # Create a local config and verify that EnvironmentBase overrides the factory default
        with open(eb.DEFAULT_CONFIG_FILENAME, 'w') as f:
            config = self._create_dummy_config('dummy')

            # Change one of the values
            original_value = eb.FACTORY_DEFAULT_CONFIG['global']['print_debug']
            config['global']['print_debug'] = not original_value

            # write out the file override
            f.write(json.dumps(config))
            f.flush()

            base = eb.EnvironmentBase(fake_cli)
            self.assertNotEqual(base.config['global']['print_debug'], original_value)

        # Make sure the file was created as requested (create_missing_files=True by default)
        self.assertTrue(os.path.isfile(eb.DEFAULT_CONFIG_FILENAME))

        # Make sure it reloaded our saved config file
        base = eb.EnvironmentBase(fake_cli)
        self.assertNotEqual(base.config['global']['print_debug'], original_value)

    def test_config_validation(self):
        """
        environmentbase.TEMPLATE_REQUIREMENTS defines the required sections and keys for a valid input config file
        This test ensures that EnvironmentBase._validate_config() enforces the TEMPLATE_REQUIREMENTS contract
        """
        valid_config = self._create_dummy_config('dummy')
        eb.EnvironmentBase._validate_config(valid_config)

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

    def test_flags(self):
        """ Verify cli flags update config object """

        # Verify that debug and output are set to the factory default
        base = eb.EnvironmentBase(self.fake_cli(['create']))
        self.assertEqual(base.config['global']['print_debug'],
                         eb.FACTORY_DEFAULT_CONFIG['global']['print_debug'])
        self.assertEqual(base.config['global']['output'],
                         eb.FACTORY_DEFAULT_CONFIG['global']['output'])

        # verify that the the debug cli flag changes the config value
        base = eb.EnvironmentBase(self.fake_cli(['create', '--debug']))
        self.assertTrue(base.config['global']['print_debug'])

        # verify that the --template-file flag changes the config value
        dummy_value = 'dummy'
        base = eb.EnvironmentBase(self.fake_cli(['create', '--template-file', dummy_value]))
        self.assertEqual(base.config['global']['output'], dummy_value)

    def test_factory_default(self):
        with self.assertRaises(IOError):
            eb.EnvironmentBase(self.fake_cli(['create']), create_missing_files=False)

        # Create refs to files that should be created and make sure they don't already exists
        config_file = os.path.join(self.temp_dir, eb.DEFAULT_CONFIG_FILENAME)
        ami_cache_file = os.path.join(self.temp_dir, eb.DEFAULT_AMI_CACHE_FILENAME)
        self.assertFalse(os.path.isfile(config_file))
        self.assertFalse(os.path.isfile(ami_cache_file))

        # Verify that create_missing_files works as intended
        eb.EnvironmentBase(self.fake_cli(['create']), create_missing_files=True)
        self.assertTrue(os.path.isfile(config_file))
        # TODO: After ami_cache is updated change 'create_missing_files' to be singular
        # self.assertTrue(os.path.isfile(ami_cache_file))

        # Verify that the previously created files are loaded up correctly
        eb.EnvironmentBase(self.fake_cli(['create']), create_missing_files=False)

    def test_controller_subclass(self):
        """ Example of out to subclass the Controller to provide additional resources """
        class MyController(eb.EnvironmentBase):
            def __init__(self, view):
                # Run parent initializer
                eb.EnvironmentBase.__init__(self, view)

            def create_action(self):
                self.initialize_template()

                # Add some stuff
                res = ec2.Instance("ec2instance", InstanceType="m3.medium", ImageId="ami-951945d0")
                self.template.add_resource(res)

                # This triggers serialization of the template and any child stacks
                self.write_tempate_to_file()

        # Initialize the the controller with faked 'create' CLI parameter
        with patch.object(sys, 'argv', ['environmentbase', 'create']):
            MyController(cli.CLI(quiet=True))

        # Load the generated output template
        with open('environmentbase.template', 'r') as f:
            template = json.load(f)

        # Verify that the ec2 instance is in the output
        self.assertTrue('ec2instance' in template['Resources'])

        # print json.dumps(template, indent=4)


    # Cloudformation doesn't currently support a dry run, so this test would create a live stack
    # def test_deploy(self):
    #     with patch.object(sys, 'argv', [
    #         'environmentbase',
    #         'deploy',
    #         '--debug',
    #         '--template_file', os.path.join(os.path.dirname(os.path.realpath(__file__)), 'resources/amzn_linux_ec2.json')]):
    #         env_base = eb.EnvironmentBase()


if __name__ == '__main__':
    main()
