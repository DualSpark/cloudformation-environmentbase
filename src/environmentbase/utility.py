import random
import string
import boto3
import json
import time
import troposphere as t
import tempfile
import os
import resources as res


def random_string(size=5):
    return ''.join(random.choice(string.ascii_lowercase + string.ascii_uppercase + string.digits) for _ in range(size))


def first_letter_capitalize(the_string):
    return the_string[:1].capitalize() + the_string[1:]


def get_type(typename):
    """
    Convert typename to type object
    :param typename: String name of type
    :return: __builtin__ type instance
    """
    types = {
        'bool': bool,
        'int': int,
        'float': float,
        # avoid all the python unicode weirdness by making all the strings basestrings
        'str': basestring,
        'basestring': basestring,
        'list': list
    }
    return types.get(typename, None)


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


def get_template_from_s3(config, template_resource_path):
    """
    Given an s3 resource path, download the template and return the json dictionary
    """
    # Download the template from s3 to a temp directory
    file_path = os.path.join(tempfile.mkdtemp(), 'downloaded_template.json')
    s3_bucket = config.get('template').get('s3_bucket')
    get_boto_client(config, "s3").download_file(s3_bucket, template_resource_path, file_path)

    # Parse the template as json and return the dictionary
    return res.load_json_file(file_path)


def get_stack_params_from_parent_template(parent_template_contents, stack_name):
    """
    This function gets all the deployment parameters used for a given stack from another deployment and returns them
    @param parent_template_contents - The loaded json contents of the parent template -- i.e., from utility.get_template_from_s3()
    @param stack_name - The name of the stack to search for in the parent template
    Returns a dictionary of stack parameters to be used with a new deployment
    """
    # Retrieve the child stack from the template
    stack_reference = parent_template_contents.get('Resources').get(stack_name)

    # If the stack is not found in the parent template, return None
    if not stack_reference:
        return None

    # Otherwise return the parameters dictionary that the stack was deployed with
    return stack_reference.get('Properties').get('Parameters')


def get_stack_depends_on_from_parent_template(parent_template_contents, stack_name):
    """
    This function gets the DependsOn attribute used for a given stack from another deployment and returns it
    @param parent_template_contents - The loaded json contents of the parent template -- i.e., from utility.get_template_from_s3()
    @param stack_name - The name of the stack to search for in the parent template
    Returns the DependsOn list to be used with a new deployment
    """
    # Retrieve the child stack from the template
    stack_reference = parent_template_contents.get('Resources').get(stack_name)

    # If the stack is not found in the parent template, return None
    if not stack_reference:
        return None

    # Otherwise return the DependsOn list that the stack was deployed with
    return stack_reference.get('DependsOn')

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

