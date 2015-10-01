import os
import os.path
import copy
import re
import botocore.exceptions
from troposphere import Parameter
from template import Template
import cli
import resources as res
from fnmatch import fnmatch
import utility
import monitor
import yaml
import logging
import json

TIMEOUT = 60
TEMPLATES_PATH = 'templates'


class ValidationError(Exception):
    pass


class EnvConfig(object):

    def __init__(self, config_handlers=None):
        self.config_handlers = config_handlers if config_handlers else []
        # self.stack_event_handlers = stack_event_handlers if stack_event_handlers else []
        # self.deploy_handlers = deploy_handlers if deploy_handlers else {}


class EnvironmentBase(object):
    """
    EnvironmentBase encapsulates functionality required to build and deploy a network and common resources for object storage within a specified region
    """

    def __init__(self,
                 view=None,
                 env_config=EnvConfig(),
                 config_filename=(res.DEFAULT_CONFIG_FILENAME + res.EXTENSIONS[0]),
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

        self.config_filename = config_filename
        self.env_config = env_config
        self.config_file_override = config_file_override
        self.config = {}
        self.globals = {}
        self.template_args = {}
        self.template = None
        self.manual_parameter_bindings = {}
        self.deploy_parameter_bindings = []
        self.ignore_outputs = ['templateValidationHash', 'dateGenerated']
        self.stack_outputs = {}
        self._config_handlers = []
        self.stack_monitor = None
        self._ami_cache = None

        self.boto_session = None

        # self.env_config = env_config
        for config_handler in env_config.config_handlers:
            self._add_config_handler(config_handler)

        # Load the user interface
        self.view = view if view else cli.CLI()

        # The view may override the config file location (i.e. command line arguments)
        if hasattr(self.view, 'config_filename') and self.view.config_filename is not None:
            self.config_filename = self.view.config_filename

        # Allow the view to execute the user's requested action
        self.view.process_request(self)

    def create_hook(self):
        """
        Override in your subclass for custom resource creation.  Called after config is loaded and template is
        initialized.  After the hook completes the templates are serialized and written to file and uploaded to S3.
        """
        pass

    def deploy_hook(self):
        """
        Extension point for modifying behavior of deploy action. Called after config is loaded and before
        cloudformation deploy_stack is called. Some things you can do in deploy_hook include modifying
        config or deploy_parameter_bindings or run arbitrary commands with boto.
        """
        pass

    def delete_hook(self):
        """
        Extension point for modifying behavior of delete action. Called after config is loaded and before cloudformation
        deploy_stack is called. Can be used to manage out-of-band resources with boto.
        """
        pass

    def stack_event_hook(self, event_data):
        """
        Extension point for reacting to the cloudformation stack event stream.  If global.monitor_stack is enabled in
        config this function is used to react to stack events. Once a stack is created a notification topic will begin
        emitting events to a queue.  Each event is passed to this call for further processing. Details about the event
        data can be read here:
        http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/using-cfn-listing-event-history.html
        :param event_data: The event_data hash provided the following mappings from the raw cloudformation event:
            "status" = "ResourceStatus"
            "type"   = "ResourceType"
            "name"   = "LogicalResourceId"
            "reason" = "ResourceStatusReason"
            "props"  = "ResourceProperties"
        :return bool: Indicates that processing is complete, false indicates that you are not yet done
        """
        return True

    def init_action(self):
        """
        Default init_action invoked by the CLI
        Generates config and ami_cache files
        Override in your subclass for custom initialization steps
        """
        self.generate_config()
        self.generate_ami_cache()

    def _ensure_template_dir_exists(self):
        parent_dir = TEMPLATES_PATH
        if not os.path.exists(parent_dir):
            os.makedirs(parent_dir)
        return TEMPLATES_PATH

    @staticmethod
    def serialize_templates_helper(template, s3_client, template_upload_acl, local_template_dir=None):
        resource_path = template.resource_path
        raw_json = template.to_template_json()

        for child, _, _ in template._child_templates:
            EnvironmentBase.serialize_templates_helper(child, s3_client, template_upload_acl, local_template_dir)

        s3_client.Bucket(Template.template_bucket).put_object(
            Key=resource_path,
            Body=raw_json,
            ACL=template_upload_acl
        )

        stack_url = utility.template_s3_url(Template.template_bucket, resource_path)
        print "Writing template to", stack_url

        if local_template_dir:
            local_file_name = template.name + '.template'
            local_file_path = os.path.join(local_template_dir, local_file_name)
            with open(local_file_path, 'w') as output_file:
                reloaded_template = json.loads(raw_json)
                json.dumps(reloaded_template, output_file, indent=4, separators=(',', ':'))

                print "Local:", local_file_path

        print

    def serialize_templates(self):
        s3_client = utility.get_boto_resource(self.config, 's3')

        local_file_path = None
        if self.globals['print_debug']:
            local_file_path = self._ensure_template_dir_exists()

        EnvironmentBase.serialize_templates_helper(
            self.template, s3_client,
            self.template_args.get('s3_upload_acl'),
            local_template_dir=local_file_path)

    def estimate_cost(self, template_name=None, template_url=None, stack_params=None):
        cfn_conn = utility.get_boto_client(self.config, 'cloudformation')

        if not template_url:
            return None

        estimate_cost_url = cfn_conn.estimate_template_cost(
            TemplateURL=template_url,
            Parameters=stack_params)
        # else:
        #     template_body = self._load_template(template_name)
        #     estimate_cost_url = cfn_conn.estimate_template_cost(
        #         TemplateBody=template_body,
        #         Parameters=stack_params)

        return estimate_cost_url.get('Url')

    def _template_url(self):
        environment_name = self.globals['environment_name']
        prefix = self.template_args["s3_prefix"]
        template_resource_path = utility.template_s3_resource_path(prefix, environment_name, include_timestamp=False)

        bucket = self.template_args['s3_bucket']
        template_url = utility.template_s3_url(bucket, template_resource_path)

        return template_url

    def create_action(self):
        """
        Default create_action invoked by the CLI
        Initializes a new template instance, and write it to file.
        """
        self.load_config()

        self.initialize_template()

        # Do custom troposphere resource creation in your overridden copy of this method
        self.create_hook()

        self.serialize_templates()

    def _ensure_stack_is_deployed(self, stack_name='UnnamedStack', sns_topic=None, stack_params=[]):
        is_successful = False
        notification_arns = []

        if sns_topic:
            notification_arns.append(sns_topic.arn)

        template_url = self._template_url()

        cfn_conn = utility.get_boto_client(self.config, 'cloudformation')
        try:
            cfn_conn.update_stack(
                StackName=stack_name,
                TemplateURL=template_url,
                Parameters=stack_params,
                NotificationARNs=notification_arns,
                Capabilities=['CAPABILITY_IAM'])
            is_successful = True
            print "Successfully issued update stack command for %s\n" % stack_name

        # Else stack doesn't currently exist, create a new stack
        except botocore.exceptions.ClientError as update_e:
            if "does not exist" in update_e.message:
                try:
                    cfn_conn.create_stack(
                        StackName=stack_name,
                        TemplateURL=template_url,
                        Parameters=stack_params,
                        NotificationARNs=notification_arns,
                        Capabilities=['CAPABILITY_IAM'],
                        DisableRollback=True,
                        TimeoutInMinutes=TIMEOUT)
                    is_successful = True
                    print "Successfully issued create stack command for %s\n" % stack_name
                except botocore.exceptions.ClientError as create_e:
                    print "Deploy failed: \n\n%s\n" % create_e.message
            else:
                raise

        return is_successful

    def add_parameter_binding(self, key, value):
        """
        The deploy_parameter_bindings is populated with hashes of the form:
         {
             'ParameterKey': <key>,
             'ParameterValue': <value>
         }

        :param key: String representing an input Parameter name in the root template
        :param value: Troposphere value for the Parameter
        """
        self.deploy_parameter_bindings.append({
            'ParameterKey': key,
            'ParameterValue': value
        })

    def deploy_action(self):
        """
        Default deploy_action invoked by the CLI. Attempt to update the stack. If the stack does not yet exist, it will
        issue a create-stack command.
        """
        self.load_config()
        self.deploy_hook()

        stack_name = self.config['global']['environment_name']

        # initialize stack event monitor
        topic = None
        queue = None
        if self.stack_monitor and self.stack_monitor.has_handlers():
            (topic, queue) = self.stack_monitor.setup_stack_monitor(self.config)

        try:
            # First try to do an update-stack... if it doesn't exist, then try create-stack
            is_successful = self._ensure_stack_is_deployed(
                stack_name,
                sns_topic=topic,
                stack_params=self.deploy_parameter_bindings)

            if self.stack_monitor and is_successful:
                self.stack_monitor.start_stack_monitor(queue, stack_name, debug=self.globals['print_debug'])

        except KeyboardInterrupt:
            if self.stack_monitor:
                print 'KeyboardInterrupt: calling cleanup'
                self.stack_monitor.cleanup_stack_monitor(topic, queue)
            raise

        if self.stack_monitor:
            self.stack_monitor.cleanup_stack_monitor(topic, queue)

    def delete_action(self):
        """
        Default delete_action invoked by CLI
        """
        self.load_config()
        self.delete_hook()

        cfn_conn = utility.get_boto_client(self.config, 'cloudformation')
        stack_name = self.config['global']['environment_name']

        cfn_conn.delete_stack(StackName=stack_name)
        print "Successfully issued delete stack command for %s\n" % stack_name

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

    def generate_config(self):
        """
        Generate config dictionary from defaults
        Add defaults from all registered config handlers (added patterns, etc.)
        Write file to self.config_filename
        """

        if os.path.isfile(self.config_filename):
            overwrite = raw_input("%s already exists. Overwrite? (y/n) " % self.config_filename).lower()
            print
            if not overwrite == 'y':
                return

        config = copy.deepcopy(res.FACTORY_DEFAULT_CONFIG)

        # Merge in any defaults provided by registered config handlers
        for handler in self._config_handlers:
            config.update(handler.get_factory_defaults())

        with open(self.config_filename, 'w') as f:
            f.write(json.dumps(config, indent=4, sort_keys=True, separators=(',', ': ')))
            print 'Generated config file at %s\n' % self.config_filename

    def load_config(self, view=None, config=None):
        """
        Load config from self.config_filename, break if it doesn't exist
        Load any overrides from environment variables
        Validate all loaded values
        """
        # Allow overriding the view for testing purposes
        if not view:
            view = self.view

        # Allow overriding of the entire config object
        if self.config_file_override:
            config = self.config_file_override

        # Else read from file
        else:
            config = res.load_file('', self.config_filename)

        # Load in cli config overrides
        view.update_config(config)

        # record value of the debug variable
        debug = config['global']['print_debug']

        # Check the environment variables for any overrides
        self._config_env_override(config, '', print_debug=debug)

        # Validate and save results
        self._validate_config(config)
        self.config = config

        # Save shortcut references to commonly referenced config sections
        self.globals = self.config.get('global', {})
        self.template_args = self.config.get('template', {})

        # Register all stack handlers
        if self.globals['monitor_stack']:
            self.stack_monitor = monitor.StackMonitor(self.globals['environment_name'])
            self.stack_monitor.add_handler(self)

        # configure Template class with S3 settings
        Template.template_bucket = self.template_args.get('s3_bucket')
        Template.s3_path_prefix = self.template_args.get("s3_prefix")
        Template.stack_timeout = self.template_args.get("timeout_in_minutes")

    def initialize_template(self):
        """
        Create new Template instance, set description and common parameters and load AMI cache.
        """
        print 'Generating template for %s stack\n' % self.globals['environment_name']

        self.template = Template(self.globals.get('environment_name', 'default_template'), root_template=True)

        self.template.description = self.template_args.get('description', 'No Description Specified')

        ec2_key = self.config.get('template').get('ec2_key_default', 'default-key')
        self.template._ec2_key = self.template.add_parameter(Parameter(
           'ec2Key',
            Type='String',
            Default=ec2_key,
            Description='Name of an existing EC2 KeyPair to enable SSH access to the instances',
            AllowedPattern=res.get_str('ec2_key'),
            MinLength=1,
            MaxLength=255,
            ConstraintDescription=res.get_str('ec2_key_message')
        ))

        self.template.add_utility_bucket(
            name=self.config.get('logging').get('s3_bucket'))

        self.manual_parameter_bindings['utilityBucket'] = self.template.utility_bucket

        ami_cache = res.load_file('', res.DEFAULT_AMI_CACHE_FILENAME)
        self.template.add_ami_mapping(ami_cache)

    def generate_ami_cache(self):
        """
        Generate ami_cache.json file from defaults
        """
        ami_cache_filename = res.DEFAULT_AMI_CACHE_FILENAME + res.EXTENSIONS[0]

        if os.path.isfile(ami_cache_filename):
            overwrite = raw_input("%s already exists. Overwrite? (y/n) " % ami_cache_filename).lower()
            print
            if not overwrite == 'y':
                return

        with open(ami_cache_filename, 'w') as f:
            f.write(json.dumps(res.FACTORY_DEFAULT_AMI_CACHE, indent=4, separators=(',', ': ')))
            print "Generated AMI cache file at %s\n" % ami_cache_filename

    def to_json(self):
        """
        Centralized method for managing outputting this template with a timestamp identifying when it was generated and for creating a SHA256 hash representing the template for validation purposes
        """
        return self.template.to_template_json()

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
    def add_child_template(self, child_template, merge=False, depends_on=[]):
        """
        Saves reference to provided template. References are processed in write_template_to_file().
        :param template: The Environmentbase Template you want to associate with the current instances
        :param depends_on: List of upstream resources that must be processes before the provided template
        :param merge: Determines whether the resource is attached as a child template or all of its resources merged
        into the current template
        """
        self.template.add_child_template(child_template, merge=merge, depends_on=depends_on)
