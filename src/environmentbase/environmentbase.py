import os
import os.path
import copy
import sys
import time
import re
import botocore.exceptions
import troposphere.cloudformation as cf
from troposphere import Ref, Parameter, GetAtt
from template import Template
import cli
import resources as res
from fnmatch import fnmatch
import utility
import monitor

# Allow comments in json if you can but at least parse regular json if not
try:
    import commentjson as json
    from commentjson import JSONLibraryException as ValueError
except ImportError:
    import json

# If you run into compatibility issues, use the regular json library instead:
import json as pure_json

TIMEOUT = 60
TEMPLATES_PATH = 'templates'


class ValidationError(Exception):
    pass


class EnvConfig(object):

    def __init__(self, config_handlers=None, stack_event_handlers=None, deploy_handlers=None):
        self.config_handlers = config_handlers if config_handlers else []
        self.stack_event_handlers = stack_event_handlers if stack_event_handlers else []
        self.deploy_handlers = deploy_handlers if deploy_handlers else {}


class EnvironmentBase(object):
    """
    EnvironmentBase encapsulates functionality required to build and deploy a network and common resources for object storage within a specified region
    """

    def __init__(self,
                 view=None,
                 env_config=EnvConfig(),
                 create_missing_files=True,
                 config_filename=res.DEFAULT_CONFIG_FILENAME,
                 config_file_override=None):
        """
        Init method for environment base creates all common objects for a given environment within the CloudFormation
        template including a network, s3 bucket and requisite policies to allow ELB Access log aggregation and
        CloudTrail log storage.
        :param view: View object to use.
        :param create_missing_files: Specifies policy to use when local files are missing.  When disabled missing files will cause an IOException
        :param config_filename: The name of the config file to load by default.  Note: User can still override this value from the CLI with '--config-file'.
        :param config: Override loading config values from file by providing config setting directly to the constructor
        """

        self.config_filename = None
        self.config = {}
        self.globals = {}
        self.template_args = {}
        self.template = None
        self.manual_parameter_bindings = {}
        self.deploy_parameter_bindings = []
        self.ignore_outputs = ['templateValidationHash', 'dateGenerated']
        self.stack_outputs = {}
        self._config_handlers = []
        self._deploy_handlers = []

        self.boto_session = None

        # self.env_config = env_config
        for config_handler in env_config.config_handlers:
            self._add_config_handler(config_handler)

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
        self.handle_local_config(view, config=config_file_override)

        self.view = view

        # Shortcut references to config sections
        self.globals = self.config.get('global', {})
        self.template_args = self.config.get('template', {})

        self.stack_monitor = monitor.StackMonitor(self.globals['environment_name'])
        for stack_handler in env_config.stack_event_handlers:
            self._add_stack_event_handler(stack_handler)

        for deploy_handler in env_config.deploy_handlers:
            self._add_deploy_handler(deploy_handler)

        # allow the view to execute the user's requested action
        self.view.process_request(self)

    def _ensure_template_dir_exists(self):
        parent_dir = TEMPLATES_PATH
        if not os.path.exists(parent_dir):
            os.makedirs(parent_dir)
        path = os.path.join(TEMPLATES_PATH, self.config['global']['output'])
        return path

    def write_template_to_file(self):
        """
        Serializes self.template to string and writes it to the file named in config['global']['output']
        """
        local_path = self._ensure_template_dir_exists()

        with open(local_path, 'w') as output_file:
            # Here to_json() loads child templates into S3
            raw_json = self.template.to_template_json()

            reloaded_template = pure_json.loads(raw_json)
            pure_json.dump(reloaded_template, output_file, indent=4, separators=(',', ':'))

    def create_action(self):
        """
        Default create_action invoked by the CLI
        Initializes a new template instance, and write it to file.
        """
        self.initialize_template()

        # Do custom troposphere resource creation in your overridden copy of this method

        self.write_template_to_file()

    def _load_root_template(self):
        # Validate existence of and read in the template file
        cfn_template_filename = os.path.join(TEMPLATES_PATH, self.config['global']['output'])
        if os.path.isfile(cfn_template_filename):
            with open(cfn_template_filename, 'r') as cfn_template_file:
                cfn_template = cfn_template_file.read()
            white_space = re.compile(r'\s+')
            cfn_template = re.sub(white_space, ' ', cfn_template)
        else:
            raise ValueError('Template at: %s not found\n' % cfn_template_filename)

        return cfn_template

    def _ensure_stack_is_deployed(self, stack_name='UnnamedStack', sns_topic=None, stack_params=[]):
        notification_arns = []

        if sns_topic:
            notification_arns.append(sns_topic.arn)

        cfn_template = self._load_root_template()
        cfn_conn = utility.get_boto_client(self.config, 'cloudformation')
        try:
            print "Updating stack '%s' ..." % stack_name
            cfn_conn.update_stack(
                StackName=stack_name,
                TemplateBody=cfn_template,
                Parameters=stack_params,
                NotificationARNs=notification_arns,
                Capabilities=['CAPABILITY_IAM'])
            print "[Update success]"

        # Else stack doesn't currently exist, create a new stack
        except botocore.exceptions.ClientError as e:
            print "[Update failed], %s\n" % e.message
            print "Trying create stack ..."
            # Load template to string
            try:
                cfn_conn.create_stack(
                    StackName=stack_name,
                    TemplateBody=cfn_template,
                    Parameters=stack_params,
                    NotificationARNs=notification_arns,
                    Capabilities=['CAPABILITY_IAM'],
                    DisableRollback=True,
                    TimeoutInMinutes=TIMEOUT)
                print "Created new CF stack %s\n" % stack_name
            except botocore.exceptions.ClientError as e:
                print "[Create failed], %s\nExiting" % e.message

    def deploy_action(self):
        """
        Default deploy_action invoked by the CLI. Attempt to update the stack. If the stack does not yet exist, it will
        issue a create-stack command.
        """

        for deploy_handler in self._deploy_handlers:
            deploy_handler.handle_deploy_event(self.deploy_parameter_bindings, self.config)

        # gather runtime parameters to be passed to create/update stack
        stack_params = []
        if self.deploy_parameter_bindings:
            stack_params.extend(self.deploy_parameter_bindings)

        stack_name = self.config['global']['environment_name']

        # initialize stack event monitor
        topic = None
        queue = None
        if self.stack_monitor.has_handlers():
            (topic, queue) = self.stack_monitor.setup_stack_monitor()

        # Get url to cost estimate calculator
        # estimate_cost_url = cfn_conn.estimate_template_cost(
        #     TemplateBody=cfn_template,
        #     Parameters=stack_params).get('Url')
        # print estimate_cost_url

        # First try to do an update-stack... if it doesn't exist, then try create-stack
        self._ensure_stack_is_deployed(
            stack_name,
            sns_topic=topic,
            stack_params=stack_params)

        try:
            self.stack_monitor.start_stack_monitor(queue, stack_name, self.config)
        except KeyboardInterrupt:
            print 'KeyboardInterrupt: calling cleanup'
            self.stack_monitor.cleanup_stack_monitor(topic, queue)
            raise

        self.stack_monitor.cleanup_stack_monitor(topic, queue)

    def delete_action(self):
        """
        Default delete_action invoked by CLI
        """
        cfn_conn = utility.get_boto_client(self.config, 'cloudformation')
        stack_name = self.config['global']['environment_name']

        print "Deleting stack '%s' ..." % stack_name,
        cfn_conn.delete_stack(StackName=stack_name)
        print "Done"

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
        config_reqs_copy = copy.deepcopy(factory_schema)

        # Merge in any requirements provided by config handlers
        for handler in self._config_handlers:
            config_reqs_copy.update(handler.get_config_schema())

        self._validate_config_helper(config_reqs_copy, config, '')

    def _add_stack_event_handler(self, handler):
        if not hasattr(handler, 'handle_stack_event') or not callable(getattr(handler, 'handle_stack_event')):
            raise ValidationError('Class %s cannot be a stack_event handler, missing handle_stack_event(event_data, config)' % type(handler).__name__ )

        self.stack_monitor.add_handler(handler)

    def _add_deploy_handler(self, handler):
        if not hasattr(handler, 'handle_deploy_event') or not callable(getattr(handler, 'handle_deploy_event')):
            raise ValidationError('Class %s cannot be a deploy handler, missing handle_deploy_event(parameter_bindings, config)' % type(handler).__name__ )

        self._deploy_handlers.append(handler)

    def _add_config_handler(self, handler):
        """
        Register classes that will augment the configuration defaults and/or validation logic here
        """

        if not hasattr(handler, 'get_factory_defaults') or not callable(getattr(handler, 'get_factory_defaults')):
            raise ValidationError('Class %s cannot be a config handler, missing get_factory_defaults()' % type(handler).__name__ )

        if not hasattr(handler, 'get_config_schema') or not callable(getattr(handler, 'get_config_schema')):
            raise ValidationError('Class %s cannot be a config handler, missing get_config_schema()' % type(handler).__name__ )

        self._config_handlers.append(handler)

    @staticmethod
    def _config_env_override(config, path, print_debug=False):
        """
        Update config value with values from the environment variables. If the environment variable exists
        the config value is replaced with it's value.

        For config parameters like template.ec2_key_default this function will expect an environment
        variable matching the <section label>_<config_key> in all caps (e.g. TEMPLATE_EC2_KEY_DEFAULT).
        For environment variables containing multiple subsections the same pattern applies.

        For example: self._update_config_from_env('db', 'password') for the config file:
        {
            ...
            'db': {
                'label1': {
                    ...
                    'password': 'changeme'
                },
                'label2': {
                    ...
                    'password': 'changeme]'
                }
            }
        }

        Would replace those two database passwords if the following is run from the shell:
        > export DB_LABEL1_PASSWORD=myvoiceismypassword12345
        > export DB_LABEL2_PASSWORD=myvoiceismyotherpassword12345
        """
        for key, val in config.iteritems():
            new_path = path + ('.' if path is not '' else '') + key
            env_name = '_'.join(new_path.split('.')).upper()

            if not isinstance(val, dict):
                env_value = os.environ.get(env_name)
                if print_debug:
                    print "Checking %s (%s)" % (env_name, new_path)

                if not env_value:
                    continue

                default_value = config.get(key)
                config[key] = env_value if env_value else default_value

                if env_value:
                    print "* Updating %s from '%s' to value of '%s'" % (new_path, default_value, env_name)

            else:
                EnvironmentBase._config_env_override(config[key], new_path, print_debug=print_debug)

    def handle_local_config(self, view, config=None):
        """
        Use local file if present, otherwise use factory values and write that to disk
        unless self.create_missing_files == false, in which case throw IOError
        """

        if not config:

            # If override config file exists, use it
            if os.path.isfile(self.config_filename):
                with open(self.config_filename, 'r') as f:
                    content = f.read()
                    try:
                        config = json.loads(content)
                    except ValueError:
                        print '%s could not be parsed' % self.config_filename
                        raise

            # If we are instructed to create fresh override file, do it
            # unless the filename is something other than DEFAULT_CONFIG_FILENAME
            elif self.create_missing_files and self.config_filename == res.DEFAULT_CONFIG_FILENAME:
                default_config_copy = copy.deepcopy(res.FACTORY_DEFAULT_CONFIG)

                # Merge in any defaults provided by registered config handlers
                for handler in self._config_handlers:
                    default_config_copy.update(handler.get_factory_defaults())

                # Don't want changes to config modifying the FACTORY_DEFAULT
                config = copy.deepcopy(default_config_copy)

                with open(self.config_filename, 'w') as f:
                    f.write(json.dumps(default_config_copy, indent=4, sort_keys=True, separators=(',', ': ')))

            # Otherwise complain
            else:
                raise IOError(self.config_filename + ' could not be found')

        # Load in cli config overrides
        view.update_config(config)

        # record value of the debug variable
        debug = config['global']['print_debug']

        # Check the environment variables for any overrides
        self._config_env_override(config, '', print_debug=debug)

        # Validate and save results
        self._validate_config(config)
        self.config = config

    def initialize_template(self):
        """
        Create new Template instance, set description and common parameters and load AMI cache.
        """
        self.template = Template(self.globals.get('output', 'default_template'))

        self.template.description = self.template_args.get('description', 'No Description Specified')
        self.init_root_template(self.template_args)
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

    def init_root_template(self, template_config):
        """
        Adds common parameters for instance creation to the CloudFormation template
        @param template_config [dict] collection of template-level configuration values to drive the setup of this method
        """
        self.template.add_parameter_idempotent(Parameter('ec2Key',
                Type='String',
                Default=template_config.get('ec2_key_default', 'default-key'),
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

        self.template.add_utility_bucket(
            name=template_config.get('utility_bucket'),
            param_binding_map=self.manual_parameter_bindings)

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

    # Called after add_child_template() has attached common parameters and some instance attributes:
    # - RegionMap: Region to AMI map, allows template to be deployed in different regions without updating AMI ids
    # - ec2Key: keyname to use for ssh authentication
    # - vpcCidr: IP block claimed by whole VPC
    # - vpcId: resource id of VPC
    # - commonSecurityGroup: sg identifier for common allowed ports (22 in from VPC)
    # - utilityBucket: S3 bucket name used to send logs to
    # - availabilityZone[1-3]: Indexed names of AZs VPC is deployed to
    # - [public|private]Subnet[0-9]: indexed and classified subnet identifiers
    #
    # and some instance attributes referencing the attached parameters:
    # - self.vpc_cidr
    # - self.vpc_id
    # - self.common_security_group
    # - self.utility_bucket
    # - self.subnets: keyed by type and index (e.g. self.subnets['public'][1])
    # - self.azs: List of parameter references
    def add_child_template(self,
                           template,
                           template_bucket=None,
                           s3_template_prefix=None,
                           template_upload_acl=None,
                           depends_on=[]):
        """
        Method adds a child template to this object's template and binds the child template parameters to properties, resources and other stack outputs
        @param template [Troposphere.Template] Troposphere Template object to add as a child to this object's template
        @param template_bucket [str] name of the bucket to upload keys to - will default to value in template_args if not present
        @param s3_template_prefix [str] s3 key name prefix to prepend to s3 key path - will default to value in template_args if not present
        @param template_upload_acl [str] name of the s3 canned acl to apply to templates uploaded to S3 - will default to value in template_args if not present
        """
        name = template.name

        self.add_common_params_to_child_template(template)
        self.load_ami_cache(template)

        template.build_hook()

        stack_url = self.upload_template(
            template,
            template_bucket=template_bucket,
            s3_template_prefix=s3_template_prefix,
            template_upload_acl=template_upload_acl)

        if name not in self.stack_outputs:
            self.stack_outputs[name] = []

        stack_params = {}

        for parameter in template.parameters.keys():
            # Manual parameter bindings single-namespace
            if parameter in self.manual_parameter_bindings:
                stack_params[parameter] = self.manual_parameter_bindings[parameter]

            # Naming scheme for identifying the AZ of a subnet (not sure if this is even used anywhere)
            elif parameter.startswith('availabilityZone'):
                stack_params[parameter] = GetAtt('privateSubnet' + parameter.replace('availabilityZone', ''), 'AvailabilityZone')

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

    def upload_template(self,
                        template,
                        template_bucket=None,
                        s3_template_prefix=None,
                        template_upload_acl=None):
        """
        Upload helper to upload this template to S3 for consumption by other templates or end users.
        @param template [Template] object to be uploaded to s3.
        @param template_bucket [string] name of the AWS S3 bucket to upload this template to.
        @param s3_template_prefix [string] key name prefix to prepend to the key name for the upload of this template.
        @param template_upload_acl [string] S3 canned ACL string value to use when setting permissions on uploaded key.
        """
        key_serial = str(int(time.time()))

        if s3_template_prefix is None:
            s3_template_prefix = self.template_args.get("s3_template_prefix")

        if template_bucket is None:
            template_bucket = self.template_args.get('template_bucket')

        if template_upload_acl is None:
            template_upload_acl = self.template_args.get('template_upload_acl')

        template_name = "%s.%s.template" % (template.name, key_serial)
        s3_path = "%s/%s" % (s3_template_prefix, template_name)
        local_path = self._ensure_template_dir_exists()

        if self.config['global']['print_debug']:
            with open(local_path, 'w') as f:
                f.write(self.to_json())

        s3 = utility.get_boto_resource(self.config, 's3')

        s3.Bucket(template_bucket).put_object(
            Key=s3_path,
            Body=template.to_json(),
            ACL=template_upload_acl
        )

        stack_url = 'https://%s.s3.amazonaws.com/%s' % (template_bucket, s3_path)
        return stack_url

if __name__ == '__main__':
    EnvironmentBase()
