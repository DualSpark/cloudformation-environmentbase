#!/usr/bin/env python
'''awsbootstrap.py

This script handles overall AWS Account setup and structure for globally-enabled, non-deployment environment assets (such as CloudTrail).

Usage:
    awsbootstrap.py [--existing_bucket <EXISTING_BUCKET>] [--bucket_region <BUCKET_REGION>] [--region <REGION>]
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
import logging
import boto
import boto.iam
import boto.sns
import boto.cloudtrail
import boto.cloudformation
import json
import string
import random
import time
import copy
from environmentbase.networkbase import NetworkBase
from troposphere import Template, Ref, Join, GetAtt, Output
import troposphere.iam as iam
import troposphere.s3 as s3

arguments = docopt(__doc__, version='devtools 0.1')

if arguments['--debug']:
    level = logging.DEBUG
else:
    level = logging.INFO

logging.basicConfig(format='%(asctime)s %(levelname)s:%(message)s', level=level)

regions = []
if arguments['--region'] == 'all':
    for region in boto.vpc.regions():
        regions.append(region.name)
else:
    for region in arguments['--region'].split(','):
        regions.append(region)

def wait_for_stack(cfconn):
  while 'IN_PROGRESS' in cfconn.describe_stacks(stack_name_or_id=arguments.get('--stack_name', 'accountBootstrapStack'))[0].stack_status:
    logging.debug('Stack ' + arguments.get('--stack_name', 'accountBootstrapStack') + ' is not yet completely deployed. Waiting 20 sec until next polling interval.')
    time.sleep(20)

t = Template()

if arguments.get('--third_parth_auth_ids', False):

    federated_auth_user = t.add_resource(iam.User('federatedAuthUser',
        Path='/federatedauth/',
        Policies=[
        iam.Policy(
          PolicyName='federationUserIAMGetList',
          PolicyDocument={
              "Version": "2012-10-17",
              "Statement": [{
                      "Sid": "Stmt1429206486000",
                      "Effect": "Allow",
                      "Action": [
                          "iam:Get*",
                          "iam:List*"],
                      "Resource": [
                          "*"]}]})]))

    t.add_output(Output('federatedAuthUser',
      Value=Ref(federated_auth_user),
      Description='Name of the AWS user that federated auth system will authenticate through.'))

    t.add_output(Output('federatedAuthUserArn',
      Value=GetAtt(federated_auth_user, 'Arn')))

    assume_role_policy_document = {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Effect": "Allow",
          "Principal": {
            "AWS": Join('', ['', GetAtt(federated_auth_user, 'Arn'), ''])
          },
          "Action": "sts:AssumeRole"
        }
      ]
    }

    federated_auth_api_keys = t.add_resource(iam.AccessKey('federatedAuthAccessKey',
        UserName=Ref(federated_auth_user)))

if arguments.get('--existing_bucket', None):
    logging_bucket = argumetns.get('--existing_bucket', None)
else:
    logging_bucket_resource = t.add_resource(s3.Bucket('cloudTrailBucket',
        DependsOn=federated_auth_user.title))
    logging_bucket = Ref(logging_bucket_resource)

t.add_output(Output('bucketName',
  Value=logging_bucket,
  Description='Name of the S3 bucket created for logging'))

role_base = {
    'DependsOn': logging_bucket_resource.title,
    'AssumeRolePolicyDocument': assume_role_policy_document,
    'Path': '/userroles/',
    'Policies': []
}

admin_args = role_base.copy()
admin_args['Policies'].append(iam.Policy(
        PolicyName='adminAccess',
        PolicyDocument={
          "Version": "2012-10-17",
          "Statement": [
            {
              "Effect": "Allow",
              "Action": "*",
              "Resource": "*"}]}))

admin_role = t.add_resource(iam.Role('adminRole', **admin_args))

t.add_output(Output('adminRole',
  Value=Ref(admin_role),
  Description='Name of the AWS role for administrative access to the Console and API'))

developer_args = role_base.copy()
developer_args['Policies'].append(iam.Policy(
      PolicyName='powerUserAccess',
      PolicyDocument={
        "Version": "2012-10-17",
        "Statement": [
          {
            "Effect": "Allow",
            "NotAction": "iam:*",
            "Resource": "*"}]}))

developer_role = t.add_resource(iam.Role('developerRole', **developer_args)

t.add_output(Output('developerRole',
  Value=Ref(developer_role),
  Description='Name of the AWS role for developer or power user access to the Console and API'))

read_only_iam_policy = iam.Policy(
        PolicyName='readOnlyAccess',
        PolicyDocument={
          "Version": "2012-10-17",
          "Statement": [
            {
              "Action": [
                "appstream:Get*",
                "autoscaling:Describe*",
                "cloudformation:DescribeStacks",
                "cloudformation:DescribeStackEvents",
                "cloudformation:DescribeStackResource",
                "cloudformation:DescribeStackResources",
                "cloudformation:GetTemplate",
                "cloudformation:List*",
                "cloudfront:Get*",
                "cloudfront:List*",
                "cloudtrail:DescribeTrails",
                "cloudtrail:GetTrailStatus",
                "cloudwatch:Describe*",
                "cloudwatch:Get*",
                "cloudwatch:List*",
                "directconnect:Describe*",
                "dynamodb:GetItem",
                "dynamodb:BatchGetItem",
                "dynamodb:Query",
                "dynamodb:Scan",
                "dynamodb:DescribeTable",
                "dynamodb:ListTables",
                "ec2:Describe*",
                "elasticache:Describe*",
                "elasticbeanstalk:Check*",
                "elasticbeanstalk:Describe*",
                "elasticbeanstalk:List*",
                "elasticbeanstalk:RequestEnvironmentInfo",
                "elasticbeanstalk:RetrieveEnvironmentInfo",
                "elasticloadbalancing:Describe*",
                "elasticmapreduce:Describe*",
                "elasticmapreduce:List*",
                "elastictranscoder:Read*",
                "elastictranscoder:List*",
                "iam:List*",
                "iam:Get*",
                "kinesis:Describe*",
                "kinesis:Get*",
                "kinesis:List*",
                "opsworks:Describe*",
                "opsworks:Get*",
                "route53:Get*",
                "route53:List*",
                "redshift:Describe*",
                "redshift:ViewQueriesInConsole",
                "rds:Describe*",
                "rds:ListTagsForResource",
                "s3:Get*",
                "s3:List*",
                "sdb:GetAttributes",
                "sdb:List*",
                "sdb:Select*",
                "ses:Get*",
                "ses:List*",
                "sns:Get*",
                "sns:List*",
                "sqs:GetQueueAttributes",
                "sqs:ListQueues",
                "sqs:ReceiveMessage",
                "storagegateway:List*",
                "storagegateway:Describe*",
                "tag:get*",
                "trustedadvisor:Describe*"
              ],
              "Effect": "Allow",
              "Resource": "*"}]})

read_only_role = t.add_resource(iam.Role('readOnlyRole',
  DependsOn=logging_bucket_resource.title,
  AssumeRolePolicyDocument=assume_role_policy_document,
  Path='/userroles/',
  Policies=[read_only_iam_policy]))

t.add_output(Output('readOnlyRole',
  Value=Ref(read_only_role),
  Description='Name of the AWS role for read only access to the Console and API'))

if arguments.get('--third_parth_auth_ids', False):

    t.add_output(Output('federatedAuthUserAccessKeyId',
      Description='AWS Access Key ID for federated auth user',
      Value=Ref(federated_auth_api_keys)))

    t.add_output(Output('federatedAuthUserSecretAccessKey',
      Description='AWS Secret Access Key for federated auth user',
      Value=GetAtt(federated_auth_api_keys, 'SecretAccessKey')))

    logging.debug('**********')
    logging.debug('Intermediate template (with creds in output): ' + t.to_json())
    logging.debug('**********')

    cfconn = boto.cloudformation.connect_to_region(arguments.get('--bucket_region', 'us-west-2'))
    logging.info('Connected to CloudFormation in region: ' + arguments.get('--bucket_region>', 'us-west-2'))
    logging.info('Starting deploy of intermediate template to gather federated authentication IAM credentials')

    cfconn.create_stack(arguments.get('--stack_name', 'accountBootstrapStack'),
        template_body=t.to_json(),
        capabilities=['CAPABILITY_IAM'])

    wait_for_stack()

    stack = cfconn.describe_stacks(stack_name_or_id=arguments.get('--stack_name', 'accountBootstrapStack'))[0]
    logging.info('Stack ' + arguments.get('--stack_name', 'accountBootstrapStack') + ' has completely deployed with status of ' + stack.stack_status)

    output_variables = {}

    if stack.stack_status == 'CREATE_COMPLETE':
      logging.info('Intermediate Stack has deployed with status ' + stack.stack_status)
      for output in stack.outputs:
        output_variables[output.key] = output.value

    del t.outputs['federatedAuthUserAccessKeyId']
    del t.outputs['federatedAuthUserSecretAccessKey']

t.add_resource(s3.BucketPolicy('cloudTrailBucketPolicy',
Bucket=logging_bucket,
PolicyDocument={
"Version": "2012-10-17",
"Statement": [
  {
    "Sid": "AWSCloudTrailAclCheck20131101",
    "Effect": "Allow",
    "Principal": {
      "AWS": [
        "arn:aws:iam::903692715234:root",
        "arn:aws:iam::859597730677:root",
        "arn:aws:iam::814480443879:root",
        "arn:aws:iam::216624486486:root",
        "arn:aws:iam::086441151436:root",
        "arn:aws:iam::388731089494:root",
        "arn:aws:iam::284668455005:root",
        "arn:aws:iam::113285607260:root",
        "arn:aws:iam::035351147821:root"
      ]
    },
    "Action": "s3:GetBucketAcl",
    "Resource": Join('', ["arn:aws:s3:::", logging_bucket])
  },
  {
    "Sid": "AWSCloudTrailWrite20131101",
    "Effect": "Allow",
    "Principal": {
      "AWS": [
        "arn:aws:iam::903692715234:root",
        "arn:aws:iam::859597730677:root",
        "arn:aws:iam::814480443879:root",
        "arn:aws:iam::216624486486:root",
        "arn:aws:iam::086441151436:root",
        "arn:aws:iam::388731089494:root",
        "arn:aws:iam::284668455005:root",
        "arn:aws:iam::113285607260:root",
        "arn:aws:iam::035351147821:root"
      ]
    },
    "Action": "s3:PutObject",
    "Resource": Join('', ["arn:aws:s3:::", logging_bucket, "/AWSLogs/", Ref('AWS::AccountId'), "/*"]),
    "Condition": {
      "StringEquals": {
        "s3:x-amz-acl": "bucket-owner-full-control"}}}]}))


  logging.debug('**********')
  logging.debug('Final template (without creds in output): ' + t.to_json())
  logging.debug('**********')

  logging.debug('Updating CloudFormation stack with final template')
  cfconn.update_stack(stack_name=arguments.get('--stack_name', 'accountBootstrapStack'),
        template_body=t.to_json(),
        capabilities=['CAPABILITY_IAM'])

  wait_for_stack()

  stack = cfconn.describe_stacks(stack_name_or_id=arguments.get('--stack_name', 'accountBootstrapStack'))[0]
  if stack.stack_status == 'UPDATE_COMPLETE':
      logging.info('Stack ' + arguments.get('--stack_name', 'accountBootstrapStack') + ' has completely deployed with status of ' + stack.stack_status)
  else:
    logging.error('Final stack failed to deploy. Please check errors in AWS console, repair CloudFormation template and redeploy. Note that credentials are currently visible in the CloudFormation Console.')
    exit(1)
else:
  logging.error('Stack failed to deploy. Please check errors in AWS console, repair CloudFormation template and redeploy.')
  exit(1)

iamconn = boto.connect_iam()
output_variables['loginUrl'] = iamconn.get_signin_url()

logging.debug('Regions:' + json.dumps(regions))

global_logging = True

for aws_region in regions:
    if aws_region in ['cn-north-1', 'us-gov-west-1']:
        logging.debug('Ignoring restricted region: ' + aws_region)
    else:
        logging.info('********************************************************************************')
        logging.info(' Starting to create CloudTrail resources in region: ' + aws_region)
        if arguments['--generate_topics']:
            logging.info('Generating topic for CloudTrail in region: ' + aws_region)
            sns = boto.sns.connect_to_region(aws_region)
            logging.debug('Connected to SNS API in region: ' + aws_region)
            arguments['topic_name'] = sns.create_topic(arguments['--topic_name'])['CreateTopicResponse']['CreateTopicResult']['TopicArn']

        ct = boto.cloudtrail.connect_to_region(aws_region)
        logging.debug('Connected to CloudTrail API in region: ' + aws_region)
        if len(ct.describe_trails(trail_name_list=[arguments.get('--trail_name', 'Default')]).get('trailList',[])) == 0:
            logging.info('Creating new trail in region ' + aws_region)
            ct.create_trail(name=arguments.get('--trail_name', 'Default'), s3_bucket_name=output_variables.get('bucketName', ''), s3_key_prefix=arguments.get('--s3_key_prefix'), sns_topic_name=arguments.get('topic_name'), include_global_service_events=global_logging)
            ct.start_logging(name=arguments.get('--trail_name', 'Default'))
            if global_logging == True:
              global_logging = False
        elif ct.get_trail_status('Default').get('IsLogging') == False:
            logging.info('Updating Trail in region ' + aws_region)
            ct.update_trail(name=arguments.get('--trail_name', 'Default'), s3_bucket_name=output_variables.get('bucketName', ''), s3_key_prefix=arguments.get('--s3_key_prefix'), sns_topic_name=arguments.get('topic_name'), include_global_service_events=global_logging)
            if global_logging == True:
              global_logging = False
            ct.start_logging(name=arguments.get('--trail_name', 'Default'))
        else:
            logging.info('Trail ' + arguments.get('--trail_name', 'Default') + ' already exists in region ' + aws_region)

logging.info('Bucket created for CloudTrail logs in ' + arguments.get('--bucket_region', 'us-west-2') + ' with name of ' + output_variables.get('bucketName', ''))

print ''
print ''
print ''
print ''
logging.info('### Process Complete ###')
if arguments.get('--third_parth_auth_ids', False):
    logging.info('    Provide the following JSON document to the team managing federated auth for provisioning:')
    print ''
    print json.dumps(output_variables)
print ''
print ''
print ''
print ''
