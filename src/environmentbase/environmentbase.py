import os
import os.path
import copy
import re
import sys
import botocore.exceptions
from boto import cloudformation, sts
from troposphere import Parameter, Output
from template import Template
import cli
import resources as res
from fnmatch import fnmatch
import utility
import monitor
import yaml
import logging
import json
import tempfile

TIMEOUT = 60


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
        self.deploy_parameter_bindings = []
        self.ignore_outputs = ['templateValidationHash', 'dateGenerated']
        self.stack_outputs = {}
        self._config_handlers = []
        self.stack_monitor = None
        self._ami_cache = None
        self.cfn_connection = None
        self.sts_credentials = None

        self.boto_session = None

        # self.env_config = env_config
        for config_handler in env_config.config_handlers:
            self._add_config_handler(config_handler)
        self.add_config_hook()

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

    def add_config_hook(self):
        """
        Override in your subclass for adding custom config handlers.  
        Called after the other config handlers have been added.  
        After the hook completes the view is loaded and started.
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

    def stack_event_hook_wrapper(self, event_data):
        """
        Write the stack outputs to file before calling the stack_event_hook that the user overrides
        """
        if self.config.get('global').get('write_stack_outputs'):
            self.write_stack_outputs_to_file(event_data)
        self.stack_event_hook(event_data)

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
            "id"     = "PhysicalResourceId"
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

    def s3_prefix(self):
        """
        Allows subclasses to modify the default s3 prefix
        """
        return self.config.get('template').get('s3_prefix')

    def stack_outputs_directory(self):
        """
        Allows subclasses to modify the default stack outputs directory
        """
        return self.config.get('global').get('stack_outputs_directory', 'stack_outputs')

    def _ensure_template_dir_exists(self):
        template_dir = self.s3_prefix()
        if not os.path.exists(template_dir):
            os.makedirs(template_dir)
        return template_dir

    @staticmethod
    def serialize_templates_helper(template, s3_client, s3_upload=True):

        # Create stack resources for template and all child templates
        raw_json = template.to_template_json()

        # Recursively iterate through each child template to serialize it and process its children
        for child, _, _ in template._child_templates:
            EnvironmentBase.serialize_templates_helper(
                template=child,
                s3_client=s3_client,
                s3_upload=s3_upload)

        if s3_upload:
            # Upload the template to the s3 bucket under the template_prefix
            s3_client.Bucket(Template.template_bucket).put_object(
                Key=template.resource_path,
                Body=raw_json,
                ACL=Template.upload_acl
            )

        # Save the template locally with the same file hierarchy as on s3
        with open(template.resource_path, 'w') as output_file:
            reloaded_template = json.loads(raw_json)
            output_file.write(json.dumps(reloaded_template, indent=4, separators=(',', ':')))

        print "Generated {} template".format(template.name)

        if s3_upload:
            print "S3:\t{}".format(utility.get_template_s3_url(Template.template_bucket, template.resource_path))

        print "Local:\t{}\n".format(template.resource_path)


    def serialize_templates(self):
        s3_client = utility.get_boto_resource(self.config, 's3')
        local_file_path = self._ensure_template_dir_exists()

        s3_upload = self.config.get('template').get('s3_upload', True)

        EnvironmentBase.serialize_templates_helper(
            template=self.template,
            s3_client=s3_client,
            s3_upload=s3_upload)

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


    def _root_template_path(self):
        """
        Construct the root template resource path
        It never includes a timestamp because we need to find it by convention in the deploy step
        """
        return utility.get_template_s3_resource_path(
            prefix=self.s3_prefix(),
            template_name=self.globals.get('environment_name'),
            include_timestamp=False)

    def _root_template_url(self):
        """
        Construct the root template S3 URL
        """
        return utility.get_template_s3_url(
            bucket_name=self.template_args.get('s3_bucket'),
            resource_path=self._root_template_path())

    def create_action(self):
        """
        Default create_action invoked by the CLI
        Loads and validates config, initializes a new template instance, and writes it to file.
        Override the create_hook in your environment to inject all of your cloudformation resources
        """
        self.load_config()
        self.initialize_template()

        # Do custom troposphere resource creation in your overridden copy of this method
        self.create_hook()

        self.serialize_templates()

    def _ensure_stack_is_deployed(self, stack_name='UnnamedStack', sns_topic=None, stack_params=[]):
        """
        Deploys the root template to cloudformation using boto
        First attempts to issue an update stack command
        If this fails because the stack does not yet exist, then issues a create stack command
        """
        is_successful = False
        notification_arns = []

        if sns_topic:
            notification_arns.append(sns_topic.arn)

        template_url = self._root_template_url()

        cfn_conn = utility.get_boto_client(self.config, 'cloudformation')
        try:
            cfn_conn.update_stack(
                StackName=stack_name,
                TemplateURL=template_url,
                Parameters=stack_params,
                NotificationARNs=notification_arns,
                Capabilities=['CAPABILITY_IAM'])
            is_successful = True
            print "\nSuccessfully issued update stack command for %s\n" % stack_name

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
                    print "\nSuccessfully issued create stack command for %s\n" % stack_name
                except botocore.exceptions.ClientError as create_e:
                    print "Deploy failed: \n\n%s\n" % create_e.message
            else:
                raise

        return is_successful

    def add_parameter_binding(self, key, value):
        """
        Deployment parameters are used to provide values for parameterized templates

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
        Default deploy_action invoked by the CLI.
        Loads and validates config, then deploys the root template to cloudformation using boto
        Override the deploy_hook in your environment to intercept the deployment process
        This can be useful for creating resources using boto outside of cloudformation
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
        Loads and validates config, then issues the delete stack command to the root stack
        Override the delete_hook in your environment to intercept the delete process with your own code
        This can be useful for deleting any resources that were created outside of cloudformation
        """
        self.load_config()
        self.delete_hook()

        cfn_conn = utility.get_boto_client(self.config, 'cloudformation')
        stack_name = self.config['global']['environment_name']

        cfn_conn.delete_stack(StackName=stack_name)
        print "\nSuccessfully issued delete stack command for %s\n" % stack_name

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

                elif isinstance(req_value, list):
                    matching_value = config[matching_key]
                    if not isinstance(matching_value, list):
                        message = "Type mismatch in config, %s should be a list, not %s" % \
                                  (new_path, type(matching_value).__name__)
                        raise ValidationError(message)

    def _validate_region(self, config):
        """
        Checks boto.region_name against the list of valid regions raising an exception if not.
        """
        valid_regions = config['global']['valid_regions']
        region_name = config['boto']['region_name']
        if region_name not in valid_regions:
            raise ValidationError('Unrecognized region name: ' + region_name)

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

        # Validate region
        self._validate_region(config)

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
        the config value is replaced with its value.

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

                if env_value is None:
                    continue

                # TODO: Need better schema validation for non-string values from env vars

                # Convert true/false strings to booleans for schema validation
                if env_value.lower() == 'true':
                    env_value = True
                elif env_value.lower() == 'false':
                    env_value = False

                default_value = config.get(key)
                config[key] = env_value if env_value is not None else default_value

                if env_value is not None:
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


    def initialize_template(self):
        """
        Create new Template instance, set description and common parameters and load AMI cache.
        """
        print '\nGenerating templates for {} stack\n'.format(self.globals['environment_name'])

        # Configure Template class with S3 settings from config
        Template.template_bucket = self.template_args.get('s3_bucket')
        Template.s3_path_prefix = self.s3_prefix()
        Template.stack_timeout = self.template_args.get("timeout_in_minutes")
        Template.upload_acl = self.template_args.get('s3_upload_acl')
        Template.include_timestamp = self.template_args.get('include_timestamp')

        # Create the root template object
        self.template = Template(self.globals.get('environment_name', 'default_template'))
        self.template.description = self.template_args.get('description', 'No Description Specified')
        self.template.resource_path = self._root_template_path()

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

        bucket_name = self.config.get('logging').get('s3_bucket')

        self.template.add_utility_bucket(name=bucket_name)
        self.template.add_output(Output('utilityBucket',Value=bucket_name))

        ami_filename = self.config['template']['ami_map_file']
        ami_cache = res.load_yaml_file(ami_filename)

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
        Centralized method for outputting the root template with a timestamp identifying when it
        was generated and for creating a SHA256 hash representing the template for validation purposes
        Also recursively processess all child templates
        """
        return self.template.to_template_json()

    # Called after add_child_template() has attached common parameters and some instance attributes:
    # - RegionMap: Region to AMI map, allows template to be deployed in different regions without updating AMI ids
    # - ec2Key: keyname to use for ssh authentication
    # - vpcCidr: IP block claimed by whole VPC
    # - vpcId: resource id of VPC
    # - commonSecurityGroup: sg identifier for common allowed ports (22 in from VPC)
    # - utilityBucket: S3 bucket name used to send logs to
    # - [public|private]Subnet[0-9]: indexed and classified subnet identifiers
    #
    # and some instance attributes referencing the attached parameters:
    # - self.vpc_cidr
    # - self.vpc_id
    # - self.common_security_group
    # - self.utility_bucket
    # - self.subnets: keyed by type, layer, and AZ index (e.g. self.subnets['public']['web'][1])
    def add_child_template(self, child_template, merge=False, depends_on=[]):
        """
        Saves reference to provided template. References are processed in write_template_to_file().
        :param child_template: The Environmentbase Template you want to associate with the current instances
        :param depends_on: List of upstream resources that must be processes before the provided template
        :param merge: Determines whether the resource is attached as a child template or all of its resources merged
        into the current template
        """
        return self.template.add_child_template(child_template, merge=merge, depends_on=depends_on)


    def write_stack_outputs_to_file(self, event_data):
        """
        Given the stack event data, determine if the stack has finished executing (CREATE_COMPLETE or UPDATE_COMPLETE)
        If it has, write the stack outputs to file
        """
        if event_data['type'] == 'AWS::CloudFormation::Stack' and \
        (event_data['status'] == 'CREATE_COMPLETE' or event_data['status'] == 'UPDATE_COMPLETE'):
            self.write_stack_output_to_file(stack_id=event_data['id'], stack_name=event_data['name'])


    def write_stack_output_to_file(self, stack_id, stack_name):
        """
        Given a CFN stack's physical resource ID, query the stack for its outputs
        Save outputs to file as JSON at ./<stack_outputs_dir>/<stack_name>.json
        """
        # Grab all the outputs from the cfn stack object as k:v pairs
        stack_outputs = {}
        for output in self.get_cfn_stack_obj(stack_id).outputs:
            stack_outputs[output.key] = output.value

        stack_outputs_dir = self.stack_outputs_directory()

        # Ensure <stack_outputs_dir> directory exists
        if not os.path.isdir(stack_outputs_dir):
            os.mkdir(stack_outputs_dir)

        # Write the JSON-formatted stack outputs to ./<stack_outputs_dir>/<stack_name>.json
        stack_output_filename = os.path.join(stack_outputs_dir, stack_name + '.json')
        with open(stack_output_filename, 'w') as output_file:
            output_file.write(json.dumps(stack_outputs, indent=4, separators=(',', ':')))

        if self.globals['print_debug']:
            print "Outputs for {0} written to {1}\n".format(stack_name, stack_output_filename)


    def get_stack_output(self, stack_id, output_name):
        """
        Given the PhysicalResourceId of a Stack and a specific output key, return the output value
        Raise an exception if the output key is not found
        Example:
            def stack_event_hook(self, event_data):
                elb_dns_name = self.get_stack_output(event_data['id'], 'ElbDnsName')
        """
        stack_obj = self.get_cfn_stack_obj(stack_id)

        for output in stack_obj.outputs:
            if output.key == output_name:
                return output.value

        # If the output wasn't found in the stack, raise an exception
        raise Exception("%s did not output %s" % (stack_obj.stack_name, output_name))


    def get_cfn_stack_obj(self, stack_id):
        """
        Given the unique physical stack ID, return exactly one cloudformation stack object
        """
        return self.get_cfn_connection().describe_stacks(stack_id)[0]


    def get_cfn_connection(self):
        """
        We persist the CFN connection so that we don't create a new session with each request
        """
        if not self.cfn_connection:
            self.cfn_connection = cloudformation.connect_to_region(self.config.get('boto').get('region_name'))
        return self.cfn_connection


    def get_sts_credentials(self, role_session_name, role_arn):
        """
        We persist the STS credentials so that we don't create a new session with each request
        """
        if not self.sts_credentials:
            sts_connection = sts.STSConnection()
            assumed_role = sts_connection.assume_role(
                role_arn=role_arn,
                role_session_name=role_session_name
            )
            self.sts_credentials = assumed_role.credentials
        return self.sts_credentials

