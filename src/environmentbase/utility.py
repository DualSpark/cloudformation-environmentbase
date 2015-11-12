import random
import string
import boto3
import json
import time
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


def get_template_s3_resource_path(prefix, template_name, include_timestamp=True):
    """
    Constructs s3 resource path for provided template name
    :param prefix: S3 base path (marts after url port and hostname)
    :param template_name: File name minus '.template' suffix and any timestamp portion
    :param include_timestamp: Indicates whether to include the current time in the file name
    :return string: Url of S3 file
    """
    if include_timestamp:
        key_serial = str(int(time.time()))
        template_name += "." + key_serial

    return "%s/%s.template" % (prefix, template_name)


def get_template_s3_url(bucket_name, resource_path):
    """
    Constructs S3 URL from bucket name and resource path.
    :param bucket_name: S3 bucket name
    :param prefix string: S3 path prefix
    :return string: S3 Url of cloudformation templates
    """
    return 'https://%s.s3.amazonaws.com/%s' % (bucket_name, resource_path)

