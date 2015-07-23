from pkg_resources import resource_string

try:
    import commentjson as json
except ImportError:
    import json


def get_json_resource(resource_name, relative_to_module_name=__name__):
    return json.loads(get_resource(resource_name, relative_to_module_name))


def get_resource(resource_name, relative_to_module_name=__name__):
    """Retrieves resource embedded in the package (even if installed as a zipped archive)."""
    return resource_string(relative_to_module_name, 'data/' + resource_name)


DEFAULT_CONFIG_FILENAME = 'config.json'
FACTORY_DEFAULT_CONFIG = get_json_resource(DEFAULT_CONFIG_FILENAME)


DEFAULT_AMI_CACHE_FILENAME = 'ami_cache.json'
FACTORY_DEFAULT_AMI_CACHE = get_json_resource(DEFAULT_AMI_CACHE_FILENAME)


CONFIG_REQUIREMENTS_FILENAME = 'config_schema.json'
CONFIG_REQUIREMENTS = get_json_resource(CONFIG_REQUIREMENTS_FILENAME)


COMMON_STRINGS_FILENAME = 'common_strings.json'
COMMON_STRINGS = get_json_resource(COMMON_STRINGS_FILENAME)


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
        'basestring': basestring
    }
    return types.get(typename, None)
