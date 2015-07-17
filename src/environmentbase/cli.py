#!/usr/bin/env python
"""
environemntbase

Tool bundle manages generation, deployment, and feedback of cloudformation resources.

Usage:
    environmentbase (create|deploy) [--config-file <FILE_LOCATION>] [--debug] [--template-file=<TEMPLATE_FILE>]

Options:
  -h --help                            Show this screen.
  -v --version                         Show version.
  --debug                              Prints parent template to console out.
  --config-file <CONFIG_FILE>          Name of json configuration file.
  --stack-name <STACK_NAME>            User-definable value for the CloudFormation stack being deployed.
  --template-file=<TEMPLATE_FILE>      Name of template to be either generated or deployed.
"""

# environemntbase (create|deploy) [--no_tests] [--config_file <FILE_LOCATION>] [--debug] [--region <REGION>]
#                 [--generate_topics] [--topic_name <TOPIC_NAME>] [--trail_name <TRAIL_NAME>]
#                 [--third_party_auth_ids] [--debug]
#
# --existing_bucket <EXISTING_BUCKET>  Indicates that an existing bucket should be used.
# --bucket_region <BUCKET_REGION>      Region in which to create the S3 bucket for cloudtrail aggregation [default: us-west-2].
# --generate_topics                    Command-line switch indicating whether topics will be generated or not [default: 0].
# --topic_name <TOPIC_NAME>            Name of the topic to create when generating SNS topics for CloudTrail [deault: cloudtrailtopic].
# --trail_name <TRAIL_NAME>            Name of the trail to create within CloudTrail [default: Default].
# --region <REGION>                    Comma separated list of regions to apply this setup to [default: all].
# --third_party_auth_ids               Command-line switch indicating whether an API credential will be generated or not [default: 0].

from docopt import docopt
import version
import json

class CLI(object):

    def __init__(self, quiet=False):
        """
        CLI constructor is responsible for parsing sys.argv to collect configuration information.
        If you need to change the config file from the default name set the property 'config_filename'
        from the constructor.
        quiet: is provided to suppress output, primarily for unit testing
        """
        self.quiet = quiet

        self.args = docopt(__doc__, version='environmentbase %s' % version.__version__)

        # Parsing this config filename here is required since
        # the file is already loaded in self.update_config()
        self.config_filename = self.args.get('--config_file')

    def update_config(self, config):
        """
        The controller provides its config object containing settings loaded from file.  Potentially from the filename
        provided in the constructor above.  This function allows the CLI to override any of those values the user may
        have requested.
        """
        if self.args.get('--debug'):
            config['global']['print_debug'] = True

            if config['global']['print_debug']:
                print "CLI arguments", json.dumps(self.args, indent=4, sort_keys=True)

        template_file = self.args.get('--template_file')
        if template_file is not None:
            config['global']['output'] = template_file

    def process_request(self, controller):
        """
        Controller has finished initializing its config. This function maps user requested action to
        controller.XXX_action().  Currently supported actions: create_action() and deploy_action().
        """
        if not self.quiet:
            print ''

            controller.to_json()

        if self.args.get('create', False):
            if not self.quiet:
                print 'Generating template for %s stack' % controller.config['global']['environment_name']
                print '\nWriting template to %s\n' % controller.config['global']['output']
            controller.create_action()

        elif self.args.get('deploy', False):
            controller.deploy_action()
