import random
import string
import boto3
import json
import troposphere as t


def random_string(size=5):
    return ''.join(random.choice(string.ascii_lowercase + string.ascii_uppercase + string.digits) for _ in range(size))


def _get_boto_session(boto_config):
    if not boto_config.get('session'):
        boto_config['session'] = boto3.session.Session(region_name=boto_config['region_name'])
    return boto_config['session']


def get_boto_resource(config, service_name):
    boto_config = config['boto']
    session = _get_boto_session(boto_config)
    resource = session.resource(
        service_name,
        aws_access_key_id=boto_config['aws_access_key_id'],
        aws_secret_access_key=boto_config['aws_secret_access_key']
    )
    return resource


def get_boto_client(config, service_name):
    boto_config = config['boto']
    session = _get_boto_session(boto_config)
    client = session.client(
        service_name,
        aws_access_key_id=boto_config['aws_access_key_id'],
        aws_secret_access_key=boto_config['aws_secret_access_key']
    )

    return client


def tropo_to_string(snippet, indent=4, sort_keys=True, separators=(',', ': ')):
    """
    Returns the json representation of any troposphere object/template
    """
    return json.dumps(snippet, cls=t.awsencode, indent=indent, sort_keys=sort_keys, separators=separators)
