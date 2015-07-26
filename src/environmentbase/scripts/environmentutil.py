#!/usr/bin/env python
"""environmentutil.py
Utility tool helps to manage mappings and gathering data from across multiple AWS Availability zones.

Usage:
    environmentutil amimap get [--aws_region <AWS_REGION>]
            [--config_file <CONFIG_FILE>]
    environmentutil amimap write [<OUTPUT_FILE>] [--aws_region <AWS_REGION>]
            [--config_file <CONFIG_FILE>]
    environmentutil deploy <CLOUDFORMATION_TEMPLATE> [<PARAMETER_JSON_FILE>]
            [--aws_region <AWS_REGION>] [--config_file <CONFIG_FILE>]

Options:
  -h --help                    Show this screen.
  -v --version                 Show version.
  --aws_region <AWS_REGION>    Region to start queries to AWS API from [default: us-east-1].
  --config_file <CONFIG_FILE>  JSON Config file holding the extended configuration for this toolset [default: config_args.json].
"""
from docopt import docopt
import boto
import json
import logging
import time

__version__ = 0.1


class EnvironmentUtil(object):
    """
    EnvironmentUtil class holds common task methods for deploying, managing or
    building CloudFormation templates with the environmenbase toolset.
    """

    def __init__(self,
                 config_args):
        """
        Init for EnvironmentUtil class which persists config args as a dictionary
        @param config_args [dict] - dictionary of configuration values
        """
        self.configuration = config_args

    def get_ami_map(self,
                    aws_region=None, image_names=None):
        """
        Method iterates on all AWS regions for a given set of AMI names to gather AMI IDs and
        to create a regionmap for CloudFormation templates.
        @param aws_region [string] - optionally provides the region to start querying when gathering the list of regions globally.
        """
        if aws_region == None:
            aws_region = self.configuration.get('boto', {}).get('default_aws_region', 'us-east-1')
            logging.debug('Setting default AWS Region for API access from overall configuration [' + aws_region + ']')
        region_map = {}
        vpc_conn = boto.connect_vpc(aws_region)
        logging.debug('Connected to VPC in region [' + aws_region + ']')
        for region in vpc_conn.get_all_regions():
            if region.name not in region_map.keys():
                logging.debug('Adding region [' + region.name + '] to region map.')
                region_map[region.name] = {}
            ec2_conn = boto.connect_ec2(region.name)
            logging.debug('Connected to EC2 API in region [' + region.name + ']')
            for k, v in image_names:
                logging.debug('Looking for Image [' + k + ': ' + v + '] in region [' + region.name + ']')
                images = ec2_conn.get_all_images(filters={'name': v})
                if len(images) == 0:
                    logging.warn('No image found for [' + k + ': ' + v + '] in region [' + region.name + ']')
                elif len(images) > 1:
                    logging.warn('Found ' + str(len(images)) + ' images for [' + k + ': ' + v + '] in region [' + region.name + ']')
                else:
                    logging.debug('Adding image [' + images[0].id + '] to region [' + region.name + '] for key [' + k + ']')
                    region_map[region.name][k] = images[0].id
        logging.debug('AMI Region Map Contents: ' + json.dumps(region_map))
        return region_map

    def write_ami_map(self,
                      aws_region,
                      output_file):
        """
        Utility and convenience method for wrapping the get_ami_map method and subsequently
        writing the output to a file for use as an ami id cache.
        @param aws_region [string] - AWS-specific region name to start when querying the AWS APIs
        @param output_file [string] - file location where the ami cache is to be saved locally
        """
        with open(output_file, 'w') as f:
            logging.debug('Writing ami cache file to [' + output_file + ']')
            f.write(json.dumps(self.get_ami_map(aws_region)))

    def get_stack_status(self,
                         cf_conn,
                         stack_name):
        """
        Helper method handles edge cases when stack status doesn't exist yet or any more.
        @param cf_conn [Boto.CloudFormation.Connection] - Connection object to CloudFormation via Boto
        @param stack_name [string] - Name of the stack to check status on
        """
        api_result = cf_conn.describe_stacks(stack_name_or_id=stack_name)
        if len(api_result) == 0:
            return 'NOT_CREATED'
        else:
            return api_result[0].stack_status

    def wait_for_stack(self,
                       cf_conn,
                       stack_name,
                       sleep_time=20):
        """
        Method handles a wait loop for stack deploys to AWS. Sleep time should be ramped up (longer polls)
        when deploying multiple sets of stacks at the same time.
        Returns true when deploy is successful, false when errors occur.
        @param cf_conn [Boto.CloudFormation.Connection] - Connection object to CloudFormation via Boto
        @param stack_name [string] - Name of the stack to check status on
        @param sleep_time [int] - number of seconds to wait between polls of the AWS API for status on the specified CloudFormation stack
        """
        stack_status = self.get_stack_status(cf_conn, stack_name)
        loop_id = 0
        while 'IN_PROGRESS' in stack_status:
            if loop_id != 0:
                message = 'Stack %s is not yet completely deployed. Waiting 20 sec until next polling interval. Update query count [ %s ]' % (stack_name, str(loop_id))
                logging.info(message)
                time.sleep(sleep_time)
            stack_status = self.get_stack_status(cf_conn, stack_name)
            loop_id += 1

        if cf_conn.describe_stacks(stack_name_or_id=stack_name)[0].stack_status in ['CREATE_COMPLETE', 'UPDATE_COMPLETE']:
            return True
        else:
            return False

    def deploy_stack(self,
                     stack_name,
                     template_string_or_url,
                     capabilities=['CAPABILITY_IAM'],
                     parameters=None,
                     aws_region=None,
                     wait_for_complete=True):
        """
        Method takes a CloudFormation template string or S3 url and deploys the stack to the specified AWS region.
        @param stack_name [string] - name to use when deploying the CloudFormation stack.
        @param template_string_or_url [string] - S3 URL or CloudFormation template body to be deployed.
        @param capabiltiies [list(str)] - List of CloudFormation template capabilities to be granted to the deployed stack.
        @param parameters [dict] - dictionary of key value pairs containing overrides to template parameter defaults.
        @param aws_region [string] - AWS-specific region name to start when querying the AWS APIs
        @param wait_for_complete [boolean] - boolean indicating whether to poll for success or failure before completing the deploy process.
        """
        if aws_region == None:
            aws_region = self.configuration.get('boto', {}).get('default_aws_region', 'us-east-1')
            logging.debug('Setting default AWS Region for API access from overall configuration [' + aws_region + ']')

        logging.info('Connecting to CloudFormation in region [' + aws_region + ']')
        cf_conn = boto.connect_cloudformation(aws_region)
        logging.info('Starting deploy of stack [' + stack_name + '] to AWS in region [' + aws_region + ']')

        command_args = {'capabilities': capabilities}

        try:
            if type(template_string_or_url) == dict:
                command_args['template_body'] = json.dumps(template_string_or_url)
            else:
                template_dict = json.loads(template_string_or_url)
                command_args['template_body'] = template_string_or_url
        except:
            command_args['template_s3_url'] = template_string_or_url

        logging.debug('Calling stack deploy for [' + stack_name + '] with arguments: ' + json.dumps(command_args))
        cf_conn.create_stack(stack_name, **command_args)

        if wait_for_complete:
            if self.wait_for_stack(cf_conn, stack_name):
                logging.info('Stack [' + stack_name + '] successfully deployed to AWS in region [' + aws_region + ']')
                return True
            else:
                message = 'Stack [%s] failed to deploy to AWS in region [%s] with status [%s]' % (stack_name, aws_region, self.get_stack_status(cf_conn, stack_name))
                logging.warn(message)
                return False
        else:
            return True

if __name__ == '__main__':
    arguments = docopt(__doc__, version='environmentbase-cfn environment_util %s' % __version__)

    if arguments.get('--debug', False):
        level = 'DEBUG'
    else:
        level = 'INFO'
    logging.basicConfig(format='%(asctime)s %(levelname)s:%(message)s', level=level)

    config_file_path = arguments.get('--config_file', 'config_args.json')
    if config_file_path:
        with open(config_file_path, 'r') as f:
            logging.info('Reading configuration in from ')
            json_data = json.loads(f.read())
    else:
        json_data = {}

    if arguments.get('amimap'):
        env_util = EnvironmentUtil(json_data)

        if arguments.get('get'):
            logging.info('Getting AMI Map and printing to console.')
            logging.info(
                json.dumps(
                    env_util.get_ami_map(arguments.get('--aws_region', 'us-east-1'))))
        elif arguments.get('write'):
            file_location = arguments.get('<OUTPUT_FILE>', 'ami_cache.json')
            logging.info('Getting AMI Map and writing to file [' + file_location + ']')
            env_util.write_ami_map(arguments.get('--aws_region', 'us-east-1'), file_location)
    elif arguments.get('deploy'):
        pass
