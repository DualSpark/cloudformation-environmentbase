from pkg_resources import resource_string, resource_exists
import copy
import yaml
import json
import os
import re


# Declare R to be the singleton Resource instance
R = None


class Res(object):

    CONFIG_REQUIREMENTS_FILENAME = 'config_schema.json'
    CONFIG_FILENAME = "config.json"
    IMAGE_MAP_FILENAME = "ami_cache.json"
    INSTANCETYPE_MAP_FILENAME = "instancetype_to_arch.json"

    DEFAULT_DATA_PATH = "data"

    # Configure resource loading for yaml parser (i.e. yaml.load())
    _INCLUDE_RESOURCE_MODULE = __name__
    _INCLUDE_RESOURCE_INTERNAL_PATH = DEFAULT_DATA_PATH

    # Generated config sections to break out to separate files
    _EXTRACTED_CONFIG_SECTIONS = {
        'image_map': IMAGE_MAP_FILENAME,
        'instancetype_to_arch': INSTANCETYPE_MAP_FILENAME
    }

    # Resource cache, prevents multiple loads of the same file
    _loaded_files = {}

    def __init__(self):
        # Set PyYAML's '!include' constructor to use the file loader
        # Any function that changes this should set it back before exiting
        yaml.add_constructor("!include", Res._yaml_file_include)

    # Implimentation of "!include" directive for yaml parser to load YAML content from egg archive resource
    @staticmethod
    def _yaml_resource_include(loader, node):
        content = R.load_resource(
            node.value,
            module=Res._INCLUDE_RESOURCE_MODULE,
            internal_path=Res._INCLUDE_RESOURCE_INTERNAL_PATH)
        return yaml.load(content)

    # Implimentation of "!include" directive for yaml parser to load YAML content from filesystem resource
    @staticmethod
    def _yaml_file_include(loader, node):
        # Get the path out of the yaml file
        file_name = os.path.join(os.path.dirname(loader.name), node.value)
        if os.path.isfile(file_name):
            with file(file_name) as inputfile:
                return yaml.load(inputfile)
        else:
            raise Exception("Could not load file '%s'" % node.value)

    def load_resource(self,
                      filename,
                      module=None,
                      internal_path=None):
        """
        @param filename [string] The name of the file withn the egg archive
        @param module [string] A module name within the egg archive, 'internal_path'
        must be sibling to this location within the archive. Typically magic var '__name__'.
        @param internal_path [string] File path prepended to filename e.g. <internal_path>/<filename>
        Return content of a resource embedded within an egg archive.
        """

        # Can't set with param vaules for some reason
        if not module:
            module = Res._INCLUDE_RESOURCE_MODULE
        if not internal_path:
            internal_path = Res._INCLUDE_RESOURCE_INTERNAL_PATH

        # Attempt to retreive cached content
        key = "%s:%s:%s" % (module, internal_path, filename)
        if key in self._loaded_files:
            return self._loaded_files[key]

        filepath = os.path.join(internal_path, filename)

        if not resource_exists(module, filepath):
            raise Exception("Resource '%s' not found in module '%s'" % (filename, module))

        file_content = resource_string(module, filepath)

        # cache file_content
        self._loaded_files[key] = file_content

        return file_content

    def parse_file(self, filename, from_file=True):
        """
        Read file into python data structure from either EGG archive or local filesystem.
        Note: File may contain !include references to other files relative to the requested file.
        @param filename [string] Name of file to load.
        @param from_file [boolean] If true loades files from fs otherwise file loaded from resource
        path using _INCLUDE_RESOURCE_MODULE and _INCLUDE_RESOURCE_INTERNAL_PATH.
        """
        # Load file content from file or resource path
        if from_file:
            with file(filename) as f:
                content = f.read()
        else:
            content = self.load_resource(
                filename,
                module=Res._INCLUDE_RESOURCE_MODULE,
                internal_path=Res._INCLUDE_RESOURCE_INTERNAL_PATH)

        # Configure PyYAML to process '!include' directive with correct handler function
        if not from_file:
            yaml.add_constructor("!include", Res._yaml_resource_include)

        # parse and return
        parsed_content = yaml.load(content)

        # Set PyYAML's !include back to loading from files
        if not from_file:
            yaml.add_constructor("!include", Res._yaml_file_include)

        return parsed_content

    def load_config(self, config_filename=CONFIG_FILENAME):
        config = self.parse_file(config_filename)
        return config

    def _extract_config_section(self, config, config_key, filename, prompt=False):
        """
        Write requested config section to file and replace config value with a sentinel value to
        be processed later into a valid '!include' directive. The sentinel is a string containing
        the correct include directive.
        @parse config [list|dict] The config datastructure to be modified with a template token.
        @param config_key [string] The config key to be externalized.
        @param filename [string] The name of the file created to hold config[config_key]
        @param prompt [boolean] block for user input to abort file output if file already exists
        """

        # If file exists ask user if we should proceed
        if prompt and os.path.isfile(filename):
            overwrite = raw_input("%s already exists. Overwrite? (y/n) " % filename).lower()
            print
            if not overwrite == 'y':
                return

        section = config.get(config_key)

        # Output file
        with open(filename, 'w') as f:
            content = json.dumps(section, indent=4, separators=(',', ': '), sort_keys=True)
            f.write(content)

        config[config_key] = "!include %s" % filename

    def generate_config(self,
                        config_file=CONFIG_FILENAME,
                        output_filename=None,
                        config_handlers=list(),
                        extract_map=_EXTRACTED_CONFIG_SECTIONS,
                        prompt=False):
        """
        Copies specified yaml/json file from the EGG resource to current directory, default is 'conifg.json'.  Optionally
        split out specific sections into separate files using extract_map.  Additionally us config_handlers to add in
        additional conifg content before serializing content to file.
        @param config_file [string] Name of file within resource path to load.
        @param output_file [string] Name of generated config file (default is same as 'config_file')
        @param prompt [boolean] block for user input to abort file output if file already exists
        @param extract_map [map<string, string>] Specifies top-level sections of config to externalize to separate file.
        Where key=config section name, value=filename.
        @param config_handlers [list(objects)] Config handlers should resemble the following:
            class CustomHandler(object):
                @staticmethod
                def get_factory_defaults():
                    return custom_config_addition
                @staticmethod
                def get_config_schema():
                    return custom_config_validation
        """
        # Output same file name as the input unless specified otherwise
        if not output_filename:
            output_filename = config_file

        # Load config from egg
        config = self.parse_file(config_file, from_file=False)

        # Merge in any defaults provided by registered config handlers
        for handler in config_handlers:
            config.update(handler.get_factory_defaults())

        # Make changes to a new copy of the config
        config_copy = copy.deepcopy(config)

        # Since the !include references are not standard json we need to use special values we can
        # find and replace after serializing to string.

        # Write config sections to file and replace content with "!include" string.
        for section_key, filename in extract_map.iteritems():
            self._extract_config_section(config_copy, section_key, filename, prompt)
            print "Generated %s file at %s\n" % (section_key, filename)

        # Serialize config to string
        templatized_config_string = json.dumps(config_copy, indent=4, separators=(',', ': '), sort_keys=True)

        # Replace encoded 'include' with the real one using regex.
        # This amounts to capturing the quoted string and stripping off the quotes
        final_config_string = re.sub(r"\"!include ([a-zA-Z0-9_.\\-]*)\"",
                                     lambda m: m.group(0)[1:-1],
                                     templatized_config_string)

        # If file exists ask user if we should proceed
        if prompt and os.path.isfile(output_filename):
            overwrite = raw_input("%s already exists. Overwrite? (y/n) " % output_filename).lower()
            print
            if not overwrite == 'y':
                return

        # Finally write config.json to file
        with open(output_filename, 'w') as f:
            f.write(final_config_string)
            print "Generated config file at %s\n" % 'config.json'

        return final_config_string


# Assign singleton Resource instance now that the class is defined
R = Res()

COMMON_STRINGS_FILENAME = 'common_strings.json'
COMMON_STRINGS = R.parse_file(COMMON_STRINGS_FILENAME, from_file=False)


def get_str(key, default=None):
    return COMMON_STRINGS.get(key, default)
