#!/usr/bin/env python
'''Generate parameters for given template with 
values from prereq_stack(s) that have already been deployed.

Usage:
    ./get_parameters.py <s3_root_template> [<prereq_stacks>...] [--extra-yaml=FILE...] [--output=cli|json]

Options:
    -h --help               Show this screen.
    -v --version            Show version.
    s3_root_template        Like: "s3://tropos-bucket/templates/v2/microservices-root.template"
    --output=<format>       Specify output format [default: json]

Examples: 
    python get_parameters.py s3://tropos-bucket/templates/v2/microservices-root.template networkbase --output=cli
    python get_parameters.py s3://tropos-bucket/templates/v2/microservices-root.template networkbase user-runtime-root  --output=cli
    $ python get_parameters.py s3://tropos-bucket/templates/v2/microservices-root.template networkbase user-runtime-root | jq .
    [
      {
        "ParameterValue": "sg-bc973edb",
        "ParameterKey": "SwarmManagerSecurityGroupId"
      }, [...]
    ]
    $ python get_parameters.py s3://tropos-bucket/templates/v2/microservices-root.template networkbase user-runtime-root --output cli
        ParameterKey=SwarmManagerSecurityGroupId,ParameterValue=sg-bc973edb ...
'''
import urlparse
import logging
import json
import yaml

from docopt import docopt
import boto3

logging.basicConfig()
logger = logging.getLogger('get_parameters.py')
# logger.setLevel(logging.DEBUG)

CLI_FORMAT = 'ParameterKey={key},ParameterValue={value}'
JSON_FORMAT = {'ParameterKey': None, 'ParameterValue': None}

client_s3 = boto3.client('s3')
client_cfn = boto3.client('cloudformation')

def main(arguments):
    s3_root_template = arguments['<s3_root_template>']
    prereq_stacks = arguments['<prereq_stacks>']
    output_format = arguments['--output']
    extra_yamls = arguments['--extra-yaml']

    parameters = get_root_params(s3_root_template)
    additional_outputs = get_additional_outputs(prereq_stacks)
    additional_parameters = get_additional_parameters(extra_yamls)
    additional_outputs.update(additional_parameters)
    final_parameters = dict(get_final_parameters(parameters, additional_outputs))
    print format_final_parameters(final_parameters, output_format)

def get_root_params(s3_root_template):
    s3, bucket, key, _, _, _ = urlparse.urlparse(s3_root_template)
    key = key.strip('/')
    logger.debug( (bucket, key) )
    response = client_s3.get_object(Bucket=bucket, Key=key)
    body = response['Body']
    template = json.load(body)
    parameters = template.get('Parameters')
    logger.debug( parameters )
    return parameters


def get_additional_outputs(prereq_stacks):
    response = client_cfn.describe_stacks()
    stacks = response['Stacks']

    additional_outputs = {}
    for stack in stacks:
        if stack['StackName'] not in prereq_stacks:
            continue
        stack_outputs = stack.get('Outputs')
        stack_outputs = {d['OutputKey']: d['OutputValue'] for d in stack_outputs}
        additional_outputs.update(stack_outputs)

    logger.debug( additional_outputs )
    return additional_outputs


def get_additional_parameters(extra_yamls):
    additional_parameters = {}
    for param_file in extra_yamls:
        with open(param_file) as f:
            additional_parameters.update(yaml.load(f))
    return additional_parameters


def get_final_parameters(parameters, additional_outputs):
    # ParameterKey=KeyPairName, ParameterValue=MyKey
    # {'ParameterKey': key, 'ParameterValue': additional_outputs[key]}
    for key in parameters:
        if key in additional_outputs:
            yield key, additional_outputs[key]


def format_final_parameters(final_parameters, output_format='json'):
    if output_format == 'cli':
        return ' '.join([CLI_FORMAT.format(key=key, value=final_parameters[key]) 
            for key in final_parameters])
    else:
        return json.dumps([{'ParameterKey': key, 'ParameterValue': final_parameters[key]} 
            for key in final_parameters])


if __name__ == '__main__':
    arguments = docopt(__doc__, version='0.1')
    logger.info((arguments) )
    main(arguments)
