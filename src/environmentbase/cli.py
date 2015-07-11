#!/usr/bin/env python
'''environemntbase.py

Tool bundle manages generation, deployment, and feedback of cloudformation resources.

Usage:
    environemntbase.py <action> [--no_tests] [--config_file <FILE_LOCATION>] [--region <REGION>]
                    [--generate_topics] [--topic_name <TOPIC_NAME>] [--trail_name <TRAIL_NAME>]
                    [--third_party_auth_ids] [--debug]

Options:
  -h --help                            Show this screen.
  -v --version                         Show version.
  --debug                              Prints parent template to console out [default: 0].
  --existing_bucket <EXISTING_BUCKET>  Indicates that an existing bucket should be used.
  --bucket_region <BUCKET_REGION>      Region in which to create the S3 bucket for cloudtrail aggregation [default: us-west-2].
  --generate_topics                    Command-line switch indicating whether topics will be generated or not [default: 0].
  --topic_name <TOPIC_NAME>            Name of the topic to create when generating SNS topics for CloudTrail [deault: cloudtrailtopic].
  --trail_name <TRAIL_NAME>            Name of the trail to create within CloudTrail [default: Default].
  --region <REGION>                    Comma separated list of regions to apply this setup to [default: all].
  --stack_name <STACK_NAME>            User-definable value for the CloudFormation stack being deployed [default: accountBootstrapStack].
  --third_party_auth_ids               Command-line switch indicating whether an API credential will be generated or not [default: 0].
'''

from docopt import docopt


class CLI(object):

    def __init__(self):
        self.args = docopt(__doc__, version='environmentbase 0.1')

    def process_request(self, controller):
        print controller.to_json()
