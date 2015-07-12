#!/usr/bin/env python
'''environemntbase.py

Tool bundle manages generation, deployment, and feedback of cloudformation resources.

Usage:
    environmentbase.py create [--no_tests] [--config_file <FILE_LOCATION>] [--debug] [--template_file=<TEMPLATE_FILE>]
    environmentbase.py deploy [--no_tests] [--config_file <FILE_LOCATION>] [--debug]

Options:
  -h --help                            Show this screen.
  -v --version                         Show version.
  --debug                              Prints parent template to console out [default: 0].
  --config_file <CONFIG_FILE>          Name of json configuration file.
  --stack_name <STACK_NAME>            User-definable value for the CloudFormation stack being deployed.
  --template_file=<TEMPLATE_FILE>      Name of template to be either generated or deployed.
'''


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


class CLI(object):

    def __init__(self):
        self.args = docopt(__doc__, version='environmentbase 0.1')

    def process_request(self, controller):

        print ''

        if controller.debug:
            print self.args
            print controller.to_json()

        if self.args.get('create', False):
            print 'Generating template for %s stack' % controller.stack_name
            print '\nWriting template to %s\n' % controller.template_filename
            controller.create_action()

        elif self.args.get('deploy', False):
            controller.deploy_action()
