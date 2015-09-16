from pkg_resources import resource_string, resource_exists
import yaml
import os


def _test_filelike(parent, basename, validator):
    """
    Tests various file extension to find the requested file like resource
    :param parent: parent directory
    :param basename: portion of filename excluding the file extension
    :return: file path of the resource that exists, or None
    """
    (basename, provided_suffix) = os.path.splitext(basename)
    suffix_list = EXTENSIONS
    if not provided_suffix or provided_suffix not in suffix_list:
        suffix_list = [provided_suffix] + EXTENSIONS

    for extension in suffix_list:
        file_path = os.path.join(parent, basename + extension)
        if validator(file_path):
            return file_path

    return None


def test_resource(parent, basename, relative_to_module_name=__name__):
    resource_test = lambda file_path: resource_exists(relative_to_module_name, file_path)
    return _test_filelike(parent, basename, resource_test)


def test_file(parent, basename):
    file_test = lambda file_path: os.path.isfile(file_path)
    return _test_filelike(parent, basename, file_test)


def get_yaml_resource(resource_name, relative_to_module_name=__name__):
    """
    Get package resource as json
    """
    return yaml.load(get_resource(resource_name, relative_to_module_name))


def get_resource(resource_name, relative_to_module_name=__name__):
    """
    Retrieves resource embedded in the package (even if installed as a zipped archive).
    """
    file_path = test_resource('data', resource_name, relative_to_module_name)
    file_content = resource_string(relative_to_module_name, file_path)
    return file_content

EXTENSIONS = ['.yaml', '.yml', '.json']

DEFAULT_CONFIG_FILENAME = 'config'
FACTORY_DEFAULT_CONFIG = get_yaml_resource(DEFAULT_CONFIG_FILENAME)


DEFAULT_AMI_CACHE_FILENAME = 'ami_cache'
FACTORY_DEFAULT_AMI_CACHE = get_yaml_resource(DEFAULT_AMI_CACHE_FILENAME)


CONFIG_REQUIREMENTS_FILENAME = 'config_schema'
CONFIG_REQUIREMENTS = get_yaml_resource(CONFIG_REQUIREMENTS_FILENAME)


COMMON_STRINGS_FILENAME = 'common_strings'
COMMON_STRINGS = get_yaml_resource(COMMON_STRINGS_FILENAME)


def load_file(parent, basename):
    file_path = test_file(parent, basename)
    if not file_path:
        raise Exception("%s does not exist. Try running the init command to generate it.\n" % (basename + EXTENSIONS[0]))

    with open(file_path, 'r') as f:
        try:
            content = f.read()
            parsed_content = yaml.load(content)
        except ValueError:
            print '%s could not be parsed' % file_path
            raise

    return parsed_content


def get_str(key, default=None):
    return COMMON_STRINGS.get(key, default)


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
