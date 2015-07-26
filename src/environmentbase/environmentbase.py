import os
import os.path
import copy
import sys
import botocore.exceptions
import boto3
import troposphere.cloudformation as cf
from troposphere import Ref, Parameter, GetAtt
from template import Template
import cli
import resources as res
from fnmatch import fnmatch

# Allow comments in json if you can but at least parse regular json if not
try:
    import commentjson as json
except ImportError:
    import json


TIMEOUT = 60


class ValidationError(Exception):
    pass


class EnvironmentBase(object):
    """
    EnvironmentBase encapsulates functionality required to build and deploy a network and common resources for object storage within a specified region
    """

    config_filename = None
    config = {}
    globals = {}
    template_args = {}
    template = None
    manual_parameter_bindings = {}
    subnets = {}
    ignore_outputs = ['templateValidationHash', 'dateGenerated']
    stack_outputs = {}

    def __init__(self, view=None, create_missing_files=True, config_filename=res.DEFAULT_CONFIG_FILENAME):
        """
        Init method for environment base creates all common objects for a given environment within the CloudFormation
        template including a network, s3 bucket and requisite policies to allow ELB Access log aggregation and
        CloudTrail log storage.
        :param view: View object to use.
        :param create_missing_files: Specifies policy to use when local files are missing.  When disabled missing files will cause an IOException
        :param config_filename: The name of the config file to load by default.  Note: User can still override this value from the CLI with '--config-file'.
        """

        # Load the user interface
        if view is None:
            view = cli.CLI()

        # Config filename check has to happen now because the rest of the settings rely on having a loaded config file
        if hasattr(view, 'config_filename') and view.config_filename is not None:
            self.config_filename = view.config_filename
        else:
            self.config_filename = config_filename

        # Config location override
        self.create_missing_files = create_missing_files
        self.handle_local_config()

        # Process any global flags here before letting the view execute any requested user actions
        view.update_config(self.config)

        # Shortcut references to config sections
        self.globals = self.config.get('global', {})
        self.template_args = self.config.get('template', {})

        # Finally allow the view to execute the user's requested action
        view.process_request(self)

    def write_template_to_file(self):
        """
        Serializes self.template to string and writes it to the file named in config['global']['output']
        """
        indent = 0 if not self.config['global']['print_debug'] else 4

        with open(self.config['global']['output'], 'w') as output_file:
            # Here to_json() loads child templates into S3
            raw_json = self.template.to_template_json()

            reloaded_template = json.loads(raw_json)
            json.dump(reloaded_template, output_file, indent=indent, separators=(',', ':'))

    def create_action(self):
        """
        Default create_action invoked by the CLI
        Initializes a new template instance, and write it to file.
        """
        self.initialize_template()

        # Do custom troposphere resource creation here ... but in your overridden copy of this method


        self.write_template_to_file()

    def deploy_action(self):
        """
        Default deploy_action invoked by the CLI
        Attempt to query the status of the stack. If it already exists and is in a ready state, it will issue an
        update-stack command. If the stack does not yet exist, it will issue a create-stack command
        """
        cfn_conn = boto3.client('cloudformation')
        cfn_template_filename = self.config['global']['output']

        if os.path.isfile(cfn_template_filename):
            with open(self.config['global']['output'], 'r') as cfn_template_file:
                cfn_template = cfn_template_file.read().replace('\n', '')

        else:
            print 'Template at: %s not found\n' % cfn_template_filename
            sys.exit(1)

        stack_name = self.config['global']['environment_name']
        stack_params = [{
            'ParameterKey': 'ec2Key',
            'ParameterValue': self.config['template']['ec2_key_default']
        }]

        try:
            response = cfn_conn.describe_stacks(StackName=stack_name)

            stack = None
            for s in response.get('Stacks'):
                if s.get('StackName') == stack_name:
                    stack = s
            if not stack:
                raise Exception('Cannot find stack %s' % stack_name)

            status = stack.get('StackStatus')

            if status == 'ROLLBACK_COMPLETE':
                print 'Stack rolled back. Deleting it first.\n'
                cfn_conn.delete_stack(StackName=stack_name)
                print 'Re-run the command once it is deleted.\n'
                raise Exception('Cannot update, current state: %s' % status)

            # CREATE_COMPLETE and UPDATE_COMPLETE are both permissible states
            if not status.endswith('_COMPLETE'):
                raise Exception('Cannot update, current state: %s' % status)

            cfn_conn.update_stack(
                StackName=stack_name,
                TemplateBody=cfn_template,
                Parameters=stack_params,
                Capabilities=['CAPABILITY_IAM'])
            print "Updated existing stack %s\n" % stack_name

        except botocore.exceptions.ClientError:
            # Load template to string
            cfn_conn.create_stack(
                StackName=stack_name,
                TemplateBody=cfn_template,
                Parameters=stack_params,
                Capabilities=['CAPABILITY_IAM'],
                DisableRollback=True,
                TimeoutInMinutes=TIMEOUT)
            print "Created new CF stack %s\n" % stack_name

    def _validate_config_helper(self, schema, config, path):
        # Check each requirement
        for (req_key, req_value) in schema.iteritems():

            # Check for key match, usually only one match but parametrized keys can have multiple matches
            # Uses 'filename' match, similar to regex but only supports '?', '*', [XYZ], [!XYZ]
            filter_fun = lambda candidate_key: fnmatch(candidate_key, req_key)

            # Find all config keys matching the requirement
            matches = filter(filter_fun, config.keys())
            if not matches:
                message = "Config file missing section " + str(path) + ('.' if path is not '' else '') + req_key
                raise ValidationError(message)

            # Validate each matching config entry
            for matching_key in matches:
                new_path = path + ('.' if path is not '' else '') + matching_key

                # ------------ value check -----------
                if isinstance(req_value, basestring):
                    req_type = res.get_type(req_value)

                    if not isinstance(config[matching_key], req_type):
                        message = "Type mismatch in config, %s should be of type %s, not %s" % \
                                  (new_path, req_value, type(config[matching_key]).__name__)
                        raise ValidationError(message)
                    # else:
                    #     print "%s validated: %s == %s" % (new_path, req_value, type(config[matching_key]).__name__)

                # if the schema is nested another level .. we must go deeper
                elif isinstance(req_value, dict):
                    matching_value = config[matching_key]
                    if not isinstance(matching_value, dict):
                        message = "Type mismatch in config, %s should be a dict, not %s" % \
                                  (new_path, type(matching_value).__name__)
                        raise ValidationError(message)

                    self._validate_config_helper(req_value, matching_value, new_path)

    def _validate_config(self, config, factory_schema=res.CONFIG_REQUIREMENTS):
        """
        Compares provided dict against TEMPLATE_REQUIREMENTS. Checks that required all sections and values are present
        and that the required types match. Throws ValidationError if not valid.
        :param config: dict to be validated
        """
        # Merge in any requirements provided by subclass's
        config_reqs_copy = copy.deepcopy(factory_schema)
        config_reqs_copy.update(self.get_config_schema_hook())

        self._validate_config_helper(config_reqs_copy, config, '')

    @staticmethod
    def get_config_schema_hook():
        """
        This method is provided for subclasses to update config requirements with additional required keys and their types.
        The format is a dictionary with key values being one of bool/int/float/str/list.
        Example (yes comments are allowed):
        {
            "template": {
                // Name of json file containing mapping labels to AMI ids
                "ami_map_file": "basestring",
                "mock_upload": "bool",
            }
        }
        :return: dict of config settings to be merged into base config, match existing keys to replace.
        """
        return {}

    @staticmethod
    def get_factory_defaults_hook():
        """
        This method is provided for subclasses to update factory default config file with additional sections.
        The format is basic json (with comment support).
        {
            "template": {
                // Name of json file containing mapping labels to AMI ids
                "ami_map_file": "ami_cache.json",
                "mock_upload": false,
            }
        }
        :return: dict of config settings to be merged into base config, match existing keys to replace.
        """
        return {}

    def handle_local_config(self):
        """
        Use local file if present, otherwise use factory values and write that to disk
        unless self.create_missing_files == false, in which case throw IOError
        """

        # If override config file exists, use it
        if os.path.isfile(self.config_filename):
            with open(self.config_filename, 'r') as f:
                content = f.read()
                config = json.loads(content)

        # If we are instructed to create fresh override file, do it
        # unless the filename is something other than DEFAULT_CONFIG_FILENAME
        elif self.create_missing_files and self.config_filename == res.DEFAULT_CONFIG_FILENAME:
            # Merge in any defaults provided by subclass's
            default_config_copy = copy.deepcopy(res.FACTORY_DEFAULT_CONFIG)
            default_config_copy.update(self.get_factory_defaults_hook())

            # Don't want changes to config modifying the FACTORY_DEFAULT
            config = copy.deepcopy(default_config_copy)

            with open(self.config_filename, 'w') as f:
                f.write(json.dumps(default_config_copy, indent=4, separators=(',', ': ')))

        # Otherwise complain
        else:
            raise IOError(self.config_filename + ' could not be found')

        # Validate and save results
        self._validate_config(config)
        self.config = config

    def initialize_template(self):
        """
        Create new Template instance, set description and common parameters and load AMI cache.
        """
        self.template = Template(self.globals.get('output', 'default_template'))

        self.template.description = self.template_args.get('description', 'No Description Specified')
        self.add_common_parameters(self.template_args)
        EnvironmentBase.load_ami_cache(self.template, self.create_missing_files)

    @staticmethod
    def load_ami_cache(template, create_missing_files=True):
        """
        Method gets the ami cache from the file locally and adds a mapping for ami ids per region into the template
        This depends on populating ami_cache.json with the AMI ids that are output by the packer scripts per region
        @param template The template to attach the AMI mapping to
        @param create_missing_file File loading policy, if true
        """
        file_path = None

        # Users can provide override ami_cache in their project root
        local_amicache = os.path.join(os.getcwd(), res.DEFAULT_AMI_CACHE_FILENAME)
        if os.path.isfile(local_amicache):
            file_path = local_amicache

        # Or sibling to the executing class
        elif os.path.isfile(res.DEFAULT_AMI_CACHE_FILENAME):
            file_path = res.DEFAULT_AMI_CACHE_FILENAME

        if file_path:
            with open(file_path, 'r') as json_file:
                json_data = json.load(json_file)
        elif create_missing_files:
            json_data = res.FACTORY_DEFAULT_AMI_CACHE
            with open(res.DEFAULT_AMI_CACHE_FILENAME, 'w') as f:
                f.write(json.dumps(res.FACTORY_DEFAULT_AMI_CACHE, indent=4, separators=(',', ': ')))
        else:
            raise IOError(res.DEFAULT_AMI_CACHE_FILENAME + ' could not be found')

        template.add_ami_mapping(json_data)

    def add_common_parameters(self,
                              template_config):
        """
        Adds common parameters for instance creation to the CloudFormation template
        @param template_config [dict] collection of template-level configuration values to drive the setup of this method
        """
        self.template.add_parameter_idempotent(Parameter('ec2Key',
                Type='String',
                Default=template_config.get('ec2_key_default','default-key'),
                Description='Name of an existing EC2 KeyPair to enable SSH access to the instances',
                AllowedPattern=res.get_str('ec2_key'),
                MinLength=1,
                MaxLength=255,
                ConstraintDescription=res.get_str('ec2_key_message')))

        self.remote_access_cidr = self.template.add_parameter(Parameter('remoteAccessLocation',
                Description='CIDR block identifying the network address space that will be allowed to ingress into public access points within this solution',
                Type='String',
                Default='0.0.0.0/0',
                MinLength=9,
                MaxLength=18,
                AllowedPattern=res.get_str('cidr_regex'),
                ConstraintDescription=res.get_str('cidr_regex_message')))

    def to_json(self):
        """
        Centralized method for managing outputting this template with a timestamp identifying when it was generated and for creating a SHA256 hash representing the template for validation purposes
        """
        return self.template.to_template_json()

    def add_common_params_to_child_template(self, template):
        az_count = self.config['network']['az_count']
        subnet_types = self.config['network']['subnet_types']
        template.add_common_parameters(subnet_types, az_count)

        template.add_parameter_idempotent(Parameter(
            'ec2Key',
            Type='String',
            Default=self.config.get('template').get('ec2_key_default', 'default-key'),
            Description='Name of an existing EC2 KeyPair to enable SSH access to the instances',
            AllowedPattern=res.get_str('ec2_key'),
            MinLength=1,
            MaxLength=255,
            ConstraintDescription=res.get_str('ec2_key_message')))

    def add_child_template(self,
                           template,
                           s3_bucket=None,
                           s3_key_prefix=None,
                           s3_canned_acl=None,
                           depends_on=[]):
        """
        Method adds a child template to this object's template and binds the child template parameters to properties, resources and other stack outputs
        @param name [str] name of this template for key naming in s3
        @param template [Troposphere.Template] Troposphere Template object to add as a child to this object's template
        @param template_args [dict] key-value pair of configuration values for templates to apply to this operation
        @param s3_bucket [str] name of the bucket to upload keys to - will default to value in template_args if not present
        @param s3_key_prefix [str] s3 key name prefix to prepend to s3 key path - will default to value in template_args if not present
        @param s3_canned_acl [str] name of the s3 canned acl to apply to templates uploaded to S3 - will default to value in template_args if not present
        """
        name = template.name

        self.add_common_params_to_child_template(template)
        self.load_ami_cache(template)

        template.build_hook()

        if s3_key_prefix == None:
            s3_key_prefix = self.template_args.get('s3_key_name_prefix', '')
        if s3_bucket is None:
            s3_bucket = self.template_args.get('s3_bucket')
        stack_url = template.upload_template(
                     s3_bucket,
                     upload_key_name=name,
                     s3_key_prefix=s3_key_prefix,
                     s3_canned_acl=self.template_args.get('s3_canned_acl', 'public-read'),
                     mock_upload=self.template_args.get('mock_upload', False))

        if name not in self.stack_outputs:
            self.stack_outputs[name] = []

        stack_params = {}
        for parameter in template.parameters.keys():

            # Manual parameter bindings single-namespace
            if parameter in self.manual_parameter_bindings:
                stack_params[parameter] = self.manual_parameter_bindings[parameter]

            # Naming scheme for identifying the AZ of a subnet (not sure if this is even used anywhere)
            elif parameter.startswith('availabilityZone'):
                stack_params[parameter] = GetAtt('privateSubnet' + parameter.replace('availabilityZone',''), 'AvailabilityZone')

            # Match any child stack parameters that have the same name as this stacks **parameters**
            elif parameter in self.template.parameters.keys():
                stack_params[parameter] = Ref(self.template.parameters.get(parameter))

            # Match any child stack parameters that have the same name as this stacks **resources**
            elif parameter in self.template.resources.keys():
                stack_params[parameter] = Ref(self.template.resources.get(parameter))

            # Match any child stack parameters that have the same name as this stacks **outputs**
            # TODO: Does this even work? Child runs after parent completes?
            elif parameter in self.stack_outputs:
                stack_params[parameter] = GetAtt(self.stack_outputs[parameter], 'Outputs.' + parameter)

            # Finally if nothing else matches copy the child templates parameter to this template's parameter list
            # so the value will pass through this stack down to the child.
            else:
                stack_params[parameter] = Ref(self.template.add_parameter(template.parameters[parameter]))
        stack_name = name + 'Stack'

        stack_obj = cf.Stack(
            stack_name,
            TemplateURL=stack_url,
            Parameters=stack_params,
            TimeoutInMinutes=self.template_args.get('timeout_in_minutes', '60'),
            DependsOn=depends_on)

        return self.template.add_resource(stack_obj)

if __name__ == '__main__':
    EnvironmentBase()
