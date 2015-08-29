#!/usr/bin/env python
"""
environmentbase

Tool bundle manages generation, deployment, and feedback of cloudformation resources.

Usage:
    environmentbase (init|create|deploy|delete) [--config-file <FILE_LOCATION>] [--debug] [--template-file=<TEMPLATE_FILE>]

Options:
  -h --help                            Show this screen.
  -v --version                         Show version.
  --debug                              Prints parent template to console out.
  --config-file <CONFIG_FILE>          Name of json configuration file. Default value is config.json
  --stack-name <STACK_NAME>            User-definable value for the CloudFormation stack being deployed.
  --template-file=<TEMPLATE_FILE>      Name of template to be either generated or deployed.
"""

from docopt import docopt
import version
import json


class CLI(object):

    def __init__(self, quiet=False, doc=__doc__):
        """
        CLI constructor is responsible for parsing sys.argv to collect configuration information.
        If you need to change the config file from the default name set the property 'config_filename'
        from the constructor.
        quiet: is provided to suppress output, primarily for unit testing
        """
        self.quiet = quiet

        self.args = docopt(doc, version='environmentbase %s' % version.__version__)

        # Parsing this config filename here is required since
        # the file is already loaded in self.update_config()
        self.config_filename = self.args.get('--config-file')

    def update_config(self, config):
        """
        The controller provides its config object containing settings loaded from file.  Potentially from the filename
        provided in the constructor above.  This function allows the CLI to override any of those values the user may
        have requested.
        """
        if self.args.get('--debug'):
            config['global']['print_debug'] = True

            if not self.quiet:
                print "CLI arguments", json.dumps(self.args, indent=4, sort_keys=True)

        template_file = self.args.get('--template-file')
        if template_file is not None:
            config['global']['output'] = template_file

    def _process_request_helper(self, controller):
        if self.args.get('init', False):
            controller.init_action()

        if self.args.get('create', False):
            controller.load_config()
            controller.create_action()

        elif self.args.get('deploy', False):
            controller.load_config()
            controller.deploy_action()

        elif self.args.get('delete', False):
            controller.load_config()
            controller.delete_action()

    def process_request(self, controller):
        """
        Controller has finished initializing its config. This function maps user requested action to
        controller.XXX_action().  Currently supported actions: init_action(), create_action(), deploy_action(), delete_action().
        """
        print

        if self.args.get('--debug'):
            self._process_request_helper(controller)

        else:
            try:
                self._process_request_helper(controller)
            except Exception as e:
                print e.message

