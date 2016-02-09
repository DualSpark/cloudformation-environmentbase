from unittest2 import TestCase, main
import mock
from mock import patch
import os
import shutil
import yaml
import json
import sys
import copy
from tempfile import mkdtemp
from environmentbase import cli, resources as res, environmentbase as eb
from environmentbase import networkbase
import environmentbase.patterns.ha_nat
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

    def _create_dummy_config(self, env_base=None):
        dummy_string = 'dummy'
        dummy_bool = False
        dummy_int = 3
        dummy_list = ['A', 'B', 'C']

        config_requirements = res.R.parse_file(res.Res.CONFIG_REQUIREMENTS_FILENAME, from_file=False)

        if env_base:
            for handler in env_base.config_handlers:
                config_requirements.update(handler.get_config_schema())

        config = {}
        for (section, keys) in config_requirements.iteritems():
            config[section] = {}
            for (key, key_type) in keys.iteritems():
                if key_type == basestring.__name__ or key_type == str.__name__:
                    config[section][key] = dummy_string
                elif key_type == bool.__name__:
                    config[section][key] = dummy_bool
                elif key_type == int.__name__:
                    config[section][key] = dummy_int
                elif key_type == list.__name__:
                    config[section][key] = dummy_list

        config['boto']['region_name'] = config['global']['valid_regions'][0]
        return config

    def _create_local_file(self, name, content):
        f = open(os.path.join(self.temp_dir, name), 'a')
        f.write(content)
        f.flush()
        return f

    def test_constructor(self):
        """Make sure EnvironmentBase passes control to view to process user requests"""
        fake_cli = self.fake_cli(['init'])
        env_base = eb.EnvironmentBase(fake_cli)

        # Check that EnvironmentBase started the CLI
        fake_cli.process_request.assert_called_once_with(env_base)

    def test_alternate_view(self):
        """ More of an example of how to use your own custom view than a test """
        actions_called = {'init': 0, 'deploy': 0, 'create': 0, 'delete': 0}

        class MyView(object):

            def __init__(self):
                super(MyView, self).__init__()
                # Start an api, a web server or a rich client UI for example
                # Record user request(s), the controller will then call process_request()
                # so the can relay user requests to the appropriate controller action
                self.user_actions = ['init', 'create', 'deploy', 'delete']
                self.user_config_changes = {'output_filename': 'output.txt'}

            def update_config(self, config):
                # Update any config properties you need to
                # config['global']['print_debug'] = self.user_config_changes['debug']
                config['global']['output'] = self.user_config_changes['output_filename']

            def process_request(self, controller):

                for action in self.user_actions:
                    # if action == 'create':
                    #     controller.create_action()
                    # elif action == 'deploy':
                    #     controller.deploy_action()

                    actions_called[action] += 1

        eb.EnvironmentBase(MyView())

        self.assertEqual(actions_called['init'], 1)
        self.assertEqual(actions_called['create'], 1)
        self.assertEqual(actions_called['deploy'], 1)
        self.assertEqual(actions_called['delete'], 1)

    def test_config_yaml(self):
        """ Verify load_config can load non-default files """
        alt_config_filename = 'config.yaml'
        config = res.R.parse_file(res.Res.CONFIG_FILENAME, from_file=False)

        with open(alt_config_filename, 'w') as f:
            f.write(yaml.dump(config, default_flow_style=False))
            f.flush()

        fake_cli = self.fake_cli(['create', '--config-file', 'config.yaml'])
        base = eb.EnvironmentBase(fake_cli, config_filename=alt_config_filename)
        base.load_config()

        self.assertEqual(base.config['global']['environment_name'], 'environmentbase')

    def test_config_override(self):
        """ Make sure local config files overrides default values."""

        # Create a local config file and verify that it overrides the factory default
        config = self._create_dummy_config()

        # Change one of the values
        original_value = config['global']['environment_name']
        config['global']['environment_name'] = original_value + 'dummy'

        with open(res.Res.CONFIG_FILENAME, 'w') as f:
            f.write(yaml.dump(config))
            f.flush()

        fake_cli = self.fake_cli(['create'])
        base = eb.EnvironmentBase(fake_cli)
        base.load_config()

        self.assertNotEqual(base.config['global']['environment_name'], original_value)

        # 4) Validate local config with non-default name
        config_filename = 'not_default_name'

        # existence check
        with self.assertRaises(Exception):
            base = eb.EnvironmentBase(self.fake_cli(['create', '--config-file', config_filename]))
            base.load_config()

        # remove config.json and create the alternate config file
        os.remove(res.Res.CONFIG_FILENAME)
        self.assertFalse(os.path.isfile(res.Res.CONFIG_FILENAME))

        with open(config_filename, 'w') as f:
            f.write(yaml.dump(config))
            f.flush()
            base = eb.EnvironmentBase(self.fake_cli(['create', '--config-file', config_filename]))
            base.load_config()

        self.assertNotEqual(base.config['global']['environment_name'], original_value)

    def test_config_validation(self):
        """
        environmentbase.TEMPLATE_REQUIREMENTS defines the required sections and keys for a valid input config file
        This test ensures that EnvironmentBase._validate_config() enforces the TEMPLATE_REQUIREMENTS contract
        """
        cntrl = eb.EnvironmentBase(self.fake_cli(['create']))

        valid_config = self._create_dummy_config()
        cntrl._validate_config(valid_config)

        # Find a section with at least one required key
        section = ''
        keys = {}
        while True:
            (section, keys) = valid_config.items()[0]
            if len(keys) > 0:
                break
        assert len(keys) > 0

        # Check type error
        with self.assertRaises(eb.ValidationError):
            invalid_config = copy.deepcopy(valid_config)
            invalid_config['global']['print_debug'] = "dfhkjdshf"
            cntrl._validate_config(invalid_config)

        # Check missing key validation
        (key, value) = keys.items()[0]
        del valid_config[section][key]

        with self.assertRaises(eb.ValidationError):
            cntrl._validate_config(valid_config)

        # Check missing section validation
        del valid_config[section]

        with self.assertRaises(eb.ValidationError):
            cntrl._validate_config(valid_config)

        # Check wildcard sections
        config_reqs = res.R.parse_file(res.Res.CONFIG_REQUIREMENTS_FILENAME, from_file=False)
        extra_reqs = {'*-db': {'host': 'str', 'port': 'int'}}
        extra_reqs.update(config_reqs)

        valid_config.update({
            'my-db': {'host': 'localhost', 'port': 3306},
            'my-other-db': {'host': 'localhost', 'port': 3306}
        })

        # Check deep nested sections
        extra_reqs = {
            'lets': {
                'go': {
                    'deeper': {
                        'key': 'str'
                    }}}}
        extra_reqs.update(config_reqs)

        valid_config.update({
            'lets': {
                'go': {
                    'deeper': {
                        'key': 'super_secret_value'
                    }}}})

    def test_extending_config(self):

        # Typically this would subclass eb.Template
        class MyConfigHandler(object):
            @staticmethod
            def get_factory_defaults():
                return {'new_section': {'new_key': 'value'}}

            @staticmethod
            def get_config_schema():
                return {'new_section': {'new_key': 'basestring'}}

        class MyEnvBase(eb.EnvironmentBase):
            pass

        view = self.fake_cli(['init'])
        env_config = eb.EnvConfig(config_handlers=[MyConfigHandler])
        controller = MyEnvBase(
            view=view,
            env_config=env_config
        )
        controller.init_action()
        controller.load_config()

        # Make sure the runtime config and the file saved to disk have the new parameter
        self.assertEquals(controller.config['new_section']['new_key'], 'value')

        with open(res.Res.CONFIG_FILENAME, 'r') as f:
            external_config = yaml.load(f)
            self.assertEquals(external_config['new_section']['new_key'], 'value')

        # Check extended validation
        # recreate config file without 'new_section' and make sure it fails validation
        os.remove(res.Res.CONFIG_FILENAME)
        dummy_config = self._create_dummy_config()
        self._create_local_file(res.Res.CONFIG_FILENAME, json.dumps(dummy_config, indent=4))

        with self.assertRaises(eb.ValidationError):
            base = MyEnvBase(view=view, env_config=env_config)
            base.load_config()


    def test_generate_config(self):
        """ Verify cli flags update config object """

        # Verify that debug and output are set to the factory default
        base = eb.EnvironmentBase(self.fake_cli(['init']))
        base.init_action()
        base.load_config()
        self.assertEqual(base.config['global']['print_debug'],
                         res.FACTORY_DEFAULT_CONFIG['global']['print_debug'])
        self.assertEqual(base.config['global']['environment_name'],
                         res.FACTORY_DEFAULT_CONFIG['global']['environment_name'])

    def test_template_file_flag(self):
        # verify that the --template-file flag changes the config value
        dummy_value = 'dummy'
        base = eb.EnvironmentBase(self.fake_cli(['create', '--template-file', dummy_value]))
        base.init_action()
        base.load_config()
        self.assertEqual(base.config['global']['environment_name'], dummy_value)

    def test_config_file_flag(self):
        dummy_value = 'dummy'
        base = eb.EnvironmentBase(self.fake_cli(['create', '--config-file', dummy_value]))
        base.init_action()
        self.assertTrue(os.path.isfile(dummy_value))

    def test_factory_default(self):
        with self.assertRaises(Exception):
            base = eb.EnvironmentBase(self.fake_cli(['init']))
            base.load_config()



        # Create refs to files that should be created and make sure they don't already exists
        config_file = os.path.join(self.temp_dir, res.DEFAULT_CONFIG_FILENAME + res.EXTENSIONS[0])
        ami_cache_file = os.path.join(self.temp_dir, res.DEFAULT_AMI_CACHE_FILENAME + res.EXTENSIONS[0])
        self.assertFalse(os.path.isfile(config_file))
        self.assertFalse(os.path.isfile(ami_cache_file))

        # Verify that create_missing_files works as intended
        base = eb.EnvironmentBase(self.fake_cli(['init']))
        base.init_action()
        self.assertTrue(os.path.isfile(config_file))
        # TODO: After ami_cache is updated change 'create_missing_files' to be singular
        # self.assertTrue(os.path.isfile(ami_cache_file))

        # Verify that the previously created files are loaded up correctly
        eb.EnvironmentBase(self.fake_cli(['create']))


    # The following two tests use a create_action, which currently doesn't test correctly

    # def test_controller_subclass(self):
    #     """ Example of out to subclass the Controller to provide additional resources """
    #     class MyController(eb.EnvironmentBase):
    #         def __init__(self, view):
    #             # Run parent initializer
    #             eb.EnvironmentBase.__init__(self, view)

    #         # Add some stuff
    #         def create_hook(self):
    #             res = ec2.Instance("ec2instance", InstanceType="m3.medium", ImageId="ami-951945d0")
    #             self.template.add_resource(res)


    #     # Initialize the the controller with faked 'create' CLI parameter
    #     with patch.object(sys, 'argv', ['environmentbase', 'init']):
    #         ctrlr = MyController(cli.CLI(quiet=True))
    #         ctrlr.load_config()
    #         ctrlr.create_action()

    #         # Load the generated output template
    #         template_path = os.path.join(ctrlr._ensure_template_dir_exists(), ctrlr.config['global']['environment_name'] + '.template')

    #         with open(template_path, 'r') as f:
    #             template = yaml.load(f)

    #         # Verify that the ec2 instance is in the output
    #         self.assertTrue('ec2instance' in template['Resources'])

    #         # print json.dumps(template, indent=4)

    # def test_nat_role_customization(self):
    #     """ Example of out to subclass the Controller to provide additional resources """
    #     class MyNat(environmentbase.patterns.ha_nat.HaNat):
    #         def get_extra_policy_statements(self):
    #           return [{
    #               "Effect": "Allow",
    #               "Action": ["DummyAction"],
    #               "Resource": "*"
    #           }]

    #     class MyController(networkbase.NetworkBase):

    #         def create_nat(self, index, nat_instance_type, enable_ntp, name, extra_user_data=None):
    #             return MyNat(index, nat_instance_type, enable_ntp, name, extra_user_data)

    #     # Initialize the the controller with faked 'create' CLI parameter

    #     ctrlr = MyController((self.fake_cli(['init'])))
    #     ctrlr.init_action()
    #     ctrlr.load_config()
    #     ctrlr.create_action()

    #     # Load the generated output template
    #     template_path = os.path.join(ctrlr._ensure_template_dir_exists(), ctrlr.config['global']['environment_name'] + '.template')

    #     with open(template_path, 'r') as f:
    #         template = yaml.load(f)

    #     # Verify that the ec2 instance is in the output
    #     self.assertIn('Nat0Role', template['Resources'])
    #     self.assertIn('Properties', template['Resources']['Nat0Role'])
    #     self.assertIn('Policies', template['Resources']['Nat0Role']['Properties'])
    #     self.assertEqual(len(template['Resources']['Nat0Role']['Properties']['Policies']), 1)
    #     policy = template['Resources']['Nat0Role']['Properties']['Policies'][0];
    #     self.assertIn('PolicyDocument', policy)
    #     self.assertIn('Statement', policy['PolicyDocument'])
    #     self.assertEqual(len(policy['PolicyDocument']['Statement']), 2)
    #     self.assertEqual(policy['PolicyDocument']['Statement'][1]['Action'], ['DummyAction'])


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
