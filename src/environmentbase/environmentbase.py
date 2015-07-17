import os,os.path,hashlib,json,time,copy,sys
import boto, boto.s3, botocore.exceptions, boto3
import troposphere.iam as iam
import troposphere.ec2 as ec2
import troposphere.elasticloadbalancing as elb
import troposphere.autoscaling as autoscaling
import troposphere.cloudformation as cf
import troposphere.route53 as r53
import troposphere.constants as tpc
from troposphere import Ref, Parameter, FindInMap, Output, Base64, Join, GetAtt
from template import Template
import cli
from pkg_resources import resource_string


def _get_internal_resource(resource_name):
    """Retrieves resource embedded in the package (even if installed as a zipped archive)."""
    return json.loads(resource_string(__name__, 'data/' + resource_name))

DEFAULT_CONFIG_FILENAME = 'config.json'
DEFAULT_AMI_CACHE_FILENAME = 'ami_cache.json'

TIMEOUT = 60

FACTORY_DEFAULT_CONFIG = _get_internal_resource(DEFAULT_CONFIG_FILENAME)
FACTORY_DEFAULT_AMI_CACHE = _get_internal_resource(DEFAULT_AMI_CACHE_FILENAME)

TEMPLATE_REQUIREMENTS = {
    "global": [
        # External template name: *output* filename when running create, *input* filename when running deploy
        ('output', basestring),
        # Name of top-level stack when deploying template
        ('environment_name', basestring),
        # Prints extra information useful for debugging
        ('print_debug', bool)
    ],
    "template": [
        # Name of json file containing mapping labels to AMI ids
        ('ami_map_file', basestring)
    ]
}


# TODO: externalize this to the data dir
def _build_common_strings():
    return {
            "valid_instance_types": ["t2.micro", "t2.small", "t2.medium",
                                     "m3.medium", "m3.large", "m3.xlarge", "m3.2xlarge",
                                     "c4.large", "c4.xlarge", "c4.2xlarge", "c4.4xlarge", "c4.8xlarge",
                                     "c3.large", "c3.xlarge", "c3.2xlarge", "c3.4xlarge", "c3.8xlarge",
                                     "r3.large", "r3.xlarge", "r3.2xlarge", "r3.4xlarge", "r3.8xlarge",
                                     "i2.xlarge", "i2.2xlarge", "i2.4xlarge", "i2.8xlarge",
                                     "d2.xlarge", "d2.2xlarge", "d2.4xlarge", "d2.8xlarge",
                                     "g2.2xlarge"],
            "valid_instance_type_message": "must be a valid EC2 instance type.",
            "valid_db_instance_types": ["db.t1.micro", "db.m1.small",
                                        "db.m3.medium", "db.m3.large", "db.m3.xlarge", "db.m3.2xlarge",
                                        "db.r3.large", "db.r3.xlarge", "db.r3.2xlarge", "db.r3.4xlarge", "db.r3.8xlarge",
                                        "db.t2.micro", "db.t2.small", "db.t2.medium"],
            "valid_db_instance_type_message": "must be a valid RDS DB instance type.",
            "boolean_options": ["True", "False"],
            "cidr_regex": "(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})/(\\d{1,2})",
            "cidr_regex_message": "must be a valid IP CIDR range of the form x.x.x.x/x.",
            "ip_regex": "(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})",
            "ip_regex_message": "must be a valid IP address in the form x.x.x.x.",
            "valid_ebs_size_message" : "must be a valid EBS size between 1GB and 1024GB.",
            "url_regex": "^(https?|ftp|file)://[-a-zA-Z0-9+&@#/%?=~_|!:,.;]*[-a-zA-Z0-9+&@#/%=~_|]",
            "ec2_key": "[\\x20-\\x7E]*",
            "ec2_key_message": "can only contain ASCII characters."}


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
    strings = _build_common_strings()

    def __init__(self, view=None, create_missing_files=True, config_filename=DEFAULT_CONFIG_FILENAME):
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

    @classmethod
    def _validate_config(cls, config):
        """
        Compares provided dict against TEMPLATE_REQUIREMENTS. Checks that required all sections and values are present
        and that the required types match. Throws ValidationError if not valid.
        :param config: dict to be validated
        """
        for (section, key_reqs) in TEMPLATE_REQUIREMENTS.iteritems():
            if section not in config:
                message = "Config file missing section: ", section
                raise ValidationError(message)

            keys = config[section]
            for (required_key , key_type) in key_reqs:
                if required_key not in keys:
                    message = "Config file missing required key %s::%s" % (section, required_key)
                    raise ValidationError(message)

                # required_keys
                if not isinstance(keys[required_key], key_type):
                    message = "Type mismatch in config file key %s::%s should be of type %s, not %s" % \
                              (section, required_key, key_type.__name__, type(keys[required_key]).__name__)
                    raise ValidationError(message)

    def handle_local_config(self):
        """
        Use local file if present, otherwise use factory values and write that to disk
        unless self.create_missing_files == false, in which case throw IOError
        """

        # If override config file exists, use it
        if os.path.isfile(self.config_filename):
            with open(self.config_filename, 'r') as f:
                config = json.loads(f.read())

        # If we are instructed to create fresh override file, do it
        # unless the filename is something other than DEFAULT_CONFIG_FILENAME
        elif self.create_missing_files and self.config_filename == DEFAULT_CONFIG_FILENAME:
            config = copy.deepcopy(FACTORY_DEFAULT_CONFIG)
            with open(self.config_filename, 'w') as f:
                f.write(json.dumps(FACTORY_DEFAULT_CONFIG, indent=4, separators=(',', ': ')))

        # Otherwise complain
        else:
            raise IOError(self.config_filename + ' could not be found')

        # Validate and save results
        EnvironmentBase._validate_config(config)
        self.config = config

    def initialize_template(self):
        """
        Create new Template instance, set description and common parameters and load AMI cache.
        """
        self.template = Template(self.globals.get('output', 'default_template'))

        self.template.description = self.template_args.get('description', 'No Description Specified')
        self.add_common_parameters(self.template_args)
        self.load_ami_cache()

    def load_ami_cache(self):
        """
        Read in ami_cache file and attach AMI mapping to template. This file associates human readable handles to AMI ids.
        """
        file_path = None

        # Users can provide override ami_cache in their project root
        local_amicache = os.path.join(os.getcwd(), DEFAULT_AMI_CACHE_FILENAME)
        if os.path.isfile(local_amicache):
            file_path = local_amicache

        # Or sibling to the executing class
        elif os.path.isfile(DEFAULT_AMI_CACHE_FILENAME):
            file_path = DEFAULT_AMI_CACHE_FILENAME

        # ami_map_file = self.template_args.get('ami_map_file', file_path)
        self.add_ami_mapping(file_path)

    def add_ami_mapping(self, ami_map_file_path):
        """
        Method gets the ami cache from the file locally and adds a mapping for ami ids per region into the template
        This depends on populating ami_cache.json with the AMI ids that are output by the packer scripts per region
        @param ami_map_file [string] path representing where to find the AMI map to ingest into this template
        """
        if ami_map_file_path:
            with open(ami_map_file_path, 'r') as json_file:
                json_data = json.load(json_file)
        elif self.create_missing_files:
            json_data = FACTORY_DEFAULT_AMI_CACHE
            with open(DEFAULT_AMI_CACHE_FILENAME, 'w') as f:
                f.write(json.dumps(FACTORY_DEFAULT_AMI_CACHE, indent=4, separators=(',', ': ')))
        else:
            raise IOError(DEFAULT_AMI_CACHE_FILENAME + ' could not be found')

        for region in json_data:
            for key in json_data[region]:
                self.add_region_map_value(region, key, json_data[region][key])

    def register_elb_to_dns(self,
                            elb,
                            tier_name,
                            tier_args):
        """
        Method handles the process of uniformly creating CNAME records for ELBs in a given tier
        @param elb [Troposphere.elasticloadbalancing.LoadBalancer]
        @param tier_name [str]
        @param tier_args [dict]
        """
        if 'environmentHostedZone' not in self.template.parameters:
            hostedzone = self.template.add_parameter(Parameter(
                "environmentHostedZone",
                Description="The DNS name of an existing Amazon Route 53 hosted zone",
                Default=tier_args.get('base_hosted_zone_name', 'devopsdemo.com'),
                Type="String"))
        else:
            hostedzone = self.template.parameters.get('environmentHostedZone')

        if tier_name.lower() + 'HostName' not in self.template.parameters:
            host_name = self.template.add_parameter(Parameter(
                tier_name.lower() + 'HostName',
                Description="Friendly host name to append to the environmentHostedZone base DNS record",
                Type="String",
                Default=tier_args.get('tier_host_name', tier_name.lower())))
        else:
            host_name = self.template.parameters.get(tier_name.lower() + 'HostName')

        self.template.add_resource(r53.RecordSetType(tier_name.lower() + 'DnsRecord',
            HostedZoneName=Join('', [Ref(hostedzone), '.']),
            Comment='CNAME record for ' + tier_name.capitalize() + ' tier',
            Name=Join('', [Ref(host_name), '.', Ref(hostedzone)]),
            Type='CNAME',
            TTL='300',
            ResourceRecords=[GetAtt(elb, 'DNSName')]))

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
                AllowedPattern=self.strings.get('ec2_key'),
                MinLength=1,
                MaxLength=255,
                ConstraintDescription=self.strings.get('ec2_key_message')))

        self.remote_access_cidr = self.template.add_parameter(Parameter('remoteAccessLocation',
                Description='CIDR block identifying the network address space that will be allowed to ingress into public access points within this solution',
                Type='String',
                Default='0.0.0.0/0',
                MinLength=9,
                MaxLength=18,
                AllowedPattern=self.strings.get('cidr_regex'),
                ConstraintDescription=self.strings.get('cidr_regex_message')))

    def add_region_map_value(self,
                             region,
                             key,
                             value):
        """
        Method adds a key value pair to the RegionMap mapping within this CloudFormation template
        @param region [string] AWS region name that the key value pair is associated with
        @param key [string] name of the key to store in the RegionMap mapping for the specified Region
        @param value [string] value portion of the key value pair related to the region specified
        """
        self.__init_region_map([region])
        if region not in self.template.mappings['RegionMap']:
            self.template.mappings['RegionMap'][region] = {}
        self.template.mappings['RegionMap'][region][key] = value

    def get_logging_bucket_policy_document(self,
                                           utility_bucket,
                                           elb_log_prefix='elb_logs',
                                           cloudtrail_log_prefix='cloudtrail_logs'):
        """
        Method builds the S3 bucket policy statements which will allow the proper AWS account ids to write ELB Access Logs to the specified bucket and prefix.
        Per documentation located at: http://docs.aws.amazon.com/ElasticLoadBalancing/latest/DeveloperGuide/configure-s3-bucket.html
        @param utility_bucket [Troposphere.s3.Bucket] object reference of the utility bucket for this tier
        @param elb_log_prefix [string] prefix for paths used to prefix the path where ELB will place access logs
        """
        if elb_log_prefix != None and elb_log_prefix != '':
            elb_log_prefix = elb_log_prefix + '/'
        else:
            elb_log_prefix = ''

        if cloudtrail_log_prefix != None and cloudtrail_log_prefix != '':
            cloudtrail_log_prefix = cloudtrail_log_prefix + '/'
        else:
            cloudtrail_log_prefix = ''

        elb_accts = {'us-west-1': '027434742980',
                     'us-west-2': '797873946194',
                     'us-east-1': '127311923021',
                     'eu-west-1': '156460612806',
                     'ap-northeast-1': '582318560864',
                     'ap-southeast-1': '114774131450',
                     'ap-southeast-2': '783225319266',
                     'sa-east-1': '507241528517',
                     'us-gov-west-1': '048591011584'}

        for region in elb_accts:
            self.add_region_map_value(region, 'elbAccountId', elb_accts[region])

        statements = [{"Action" : ["s3:PutObject"],
                       "Effect" : "Allow",
                       "Resource" : Join('', ['arn:aws:s3:::', Ref(utility_bucket), '/', elb_log_prefix + 'AWSLogs/', Ref('AWS::AccountId'), '/*']),
                       "Principal" : {"AWS": [FindInMap('RegionMap', Ref('AWS::Region'), 'elbAccountId')]}},
                       {"Action" : ["s3:GetBucketAcl"],
                        "Resource" : Join('', ["arn:aws:s3:::", Ref(utility_bucket)]),
                        "Effect" : "Allow",
                            "Principal": {
                                "AWS": [
                                  "arn:aws:iam::903692715234:root",
                                  "arn:aws:iam::859597730677:root",
                                  "arn:aws:iam::814480443879:root",
                                  "arn:aws:iam::216624486486:root",
                                  "arn:aws:iam::086441151436:root",
                                  "arn:aws:iam::388731089494:root",
                                  "arn:aws:iam::284668455005:root",
                                  "arn:aws:iam::113285607260:root"]}},
                      {"Action" : ["s3:PutObject"],
                        "Resource": Join('', ["arn:aws:s3:::", Ref(utility_bucket), '/', cloudtrail_log_prefix + "AWSLogs/", Ref("AWS::AccountId"), '/*']),
                        "Effect" : "Allow",
                        "Principal": {
                            "AWS": [
                              "arn:aws:iam::903692715234:root",
                              "arn:aws:iam::859597730677:root",
                              "arn:aws:iam::814480443879:root",
                              "arn:aws:iam::216624486486:root",
                              "arn:aws:iam::086441151436:root",
                              "arn:aws:iam::388731089494:root",
                              "arn:aws:iam::284668455005:root",
                              "arn:aws:iam::113285607260:root"]},
                        "Condition": {"StringEquals" : {"s3:x-amz-acl": "bucket-owner-full-control"}}}]

        self.template.add_output(Output('elbAccessLoggingBucketAndPath',
                Value=Join('',['arn:aws:s3:::', Ref(utility_bucket), elb_log_prefix]),
                Description='S3 bucket and key name prefix to use when configuring elb access logs to aggregate to S3'))

        self.template.add_output(Output('cloudTrailLoggingBucketAndPath',
                Value=Join('',['arn:aws:s3:::', Ref(utility_bucket), cloudtrail_log_prefix]),
                Description='S3 bucket and key name prefix to use when configuring CloudTrail to aggregate logs to S3'))

        return {"Statement":statements}

    def create_asg(self,
                   layer_name,
                   instance_profile,
                   instance_type=None,
                   ami_name='ubuntu1404LtsAmiId',
                   ec2_key=None,
                   user_data=None,
                   default_instance_type=None,
                   security_groups=None,
                   min_size=1,
                   max_size=1,
                   root_volume_size=None,
                   root_volume_type=None,
                   include_ephemerals=True,
                   number_ephemeral_vols=2,
                   ebs_data_volumes=None, #[{'size':'100', 'type':'gp2', 'delete_on_termination': True, 'iops': 4000, 'volume_type': 'io1'}]
                   custom_tags=None,
                   load_balancer=None,
                   instance_monitoring=False,
                   subnet_type='private',
                   launch_config_metadata=None,
                   creation_policy=None,
                   update_policy=None,
                   depends_on=[]):
        """
        Wrapper method used to create an EC2 Launch Configuration and Auto Scaling group
        @param layer_name [string] friendly name of the set of instances being created - will be set as the name for instances deployed
        @param instance_profile [Troposphere.iam.InstanceProfile] IAM Instance Profile object to be applied to instances launched within this Auto Scaling group
        @param instance_type [Troposphere.Parameter | string] Reference to the AWS EC2 Instance Type to deploy.
        @param ami_name [string] Name of the AMI to deploy as defined within the RegionMap lookup for the deployed region
        @param ec2_key [Troposphere.Parameter | Troposphere.Ref(Troposphere.Parameter)] Input parameter used to gather the name of the EC2 key to use to secure access to instances launched within this Auto Scaling group
        @param user_data [string[]] Array of strings (lines of bash script) to be set as the user data as a bootstrap script for instances launched within this Auto Scaling group
        @param default_instance_type [string - AWS Instance Type] AWS instance type to set as the default for the input parameter defining the instance type for this layer_name
        @param security_groups [Troposphere.ec2.SecurityGroup[]] array of security groups to be applied to instances within this Auto Scaling group
        @param min_size [int] value to set as the minimum number of instances for the Auto Scaling group
        @param max_size [int] value to set as the maximum number of instances for the Auto Scaling group
        @param root_volume_size [int] size (in GiB) to assign to the root volume of the launched instance
        @param include_ephemerals [Boolean] indicates that ephemeral volumes should be included in the block device mapping of the Launch Configuration
        @param number_ephemeral_vols [int] number of ephemeral volumes to attach within the block device mapping Launch Configuration
        @param ebs_data_volumes [list] dictionary pair of size and type data properties in a list used to create ebs volume attachments
        @param custom_tags [Troposphere.autoscaling.Tag[]] Collection of Auto Scaling tags to be assigned to the Auto Scaling Group
        @param load_balancer [Troposphere.elasticloadbalancing.LoadBalancer] Object reference to an ELB to be assigned to this auto scaling group
        @param instance_monitoring [Boolean] indicates that detailed monitoring should be turned on for all instnaces launched within this Auto Scaling group
        @param subnet_type [string {'public', 'private'}] string indicating which type of subnet (public or private) instances should be launched into
        """
        if subnet_type not in ['public', 'private']:
            raise RuntimeError('Unable to determine which type of subnet instances should be launched into. ' + str(subnet_type) + ' is not one of ["public", "private"].')

        if ec2_key != None and type(ec2_key) != Ref:
            ec2_key = Ref(ec2_key)
        elif ec2_key == None:
            ec2_key = Ref(self.template.parameters['ec2Key'])

        if default_instance_type == None:
            default_instance_type = 'm1.small'

        if type(instance_type) != str:
            instance_type = Ref(instance_type)

        sg_list = []
        for sg in security_groups:
            if isinstance(sg, Ref):
                sg_list.append(sg)
            else:
                sg_list.append(Ref(sg))

        launch_config_obj = autoscaling.LaunchConfiguration(layer_name + 'LaunchConfiguration',
                IamInstanceProfile=Ref(instance_profile),
                ImageId=FindInMap('RegionMap', Ref('AWS::Region'), ami_name),
                InstanceType=instance_type,
                SecurityGroups=sg_list,
                KeyName=ec2_key,
                Metadata=(launch_config_metadata or None),
                AssociatePublicIpAddress=True if subnet_type == 'public' else False,
                InstanceMonitoring=instance_monitoring)

        if user_data != None:
            launch_config_obj.UserData=user_data

        block_devices = []
        if root_volume_type != None and root_volume_size != None:
            ebs_device = ec2.EBSBlockDevice(
                VolumeSize=root_volume_size)

            if root_volume_type != None:
                ebs_device.VolumeType=root_volume_type

            block_devices.append(ec2.BlockDeviceMapping(
                    DeviceName='/dev/sda1',
                    Ebs=ebs_device))

        device_names = ['/dev/sd%s' % c for c in 'bcdefghijklmnopqrstuvwxyz']

        if ebs_data_volumes != None and len(ebs_data_volumes) > 0:
            for ebs_volume in ebs_data_volumes:
                # Respect names provided by AMI when available
                if 'name' in ebs_volume:
                    device_name = ebs_volume.get('name')
                    device_names.remove(device_name)
                else:
                    device_name = device_names.pop()

                ebs_block_device = ec2.EBSBlockDevice(
                                DeleteOnTermination=ebs_volume.get('delete_on_termination', True),
                                VolumeSize=ebs_volume.get('size', '100'),
                                VolumeType=ebs_volume.get('type', 'gp2'))

                if 'iops' in ebs_volume:
                    ebs_block_device.Iops = int(ebs_volume.get('iops'))
                if 'snapshot_id' in ebs_volume:
                    ebs_block_device.SnapshotId = ebs_volume.get('snapshot_id')

                block_devices.append(ec2.BlockDeviceMapping(
                        DeviceName = device_name,
                        Ebs = ebs_block_device))

        if include_ephemerals and number_ephemeral_vols > 0:
            device_names.reverse()
            for x in range(0, number_ephemeral_vols):
                device_name = device_names.pop()
                block_devices.append(ec2.BlockDeviceMapping(
                            DeviceName= device_name,
                            VirtualName= 'ephemeral' + str(x)))

        if len(block_devices) > 0:
            launch_config_obj.BlockDeviceMappings = block_devices

        launch_config = self.template.add_resource(launch_config_obj)

        auto_scaling_obj = autoscaling.AutoScalingGroup(layer_name + 'AutoScalingGroup',
            AvailabilityZones=self.azs,
            LaunchConfigurationName=Ref(launch_config),
            MaxSize=max_size,
            MinSize=min_size,
            DesiredCapacity=min(min_size, max_size),
            VPCZoneIdentifier=self.subnets[subnet_type.lower()],
            TerminationPolicies=['OldestLaunchConfiguration', 'ClosestToNextInstanceHour', 'Default'],
            DependsOn=depends_on)

        lb_tmp = []

        if load_balancer is not None:
            try:
                if type(load_balancer) is dict:
                    for lb in load_balancer:
                        lb_tmp.append(Ref(load_balancer[lb]))
                elif type(load_balancer) is not Ref:
                    for lb in load_balancer:
                        lb_tmp.append(Ref(lb))
                else:
                    lb_tmp.append(load_balancer)
            except TypeError:
                lb_tmp.append(Ref(load_balancer))
        else:
            lb_tmp = None

        if lb_tmp is not None and len(lb_tmp) > 0:
            auto_scaling_obj.LoadBalancerNames = lb_tmp

        if creation_policy is not None:
            auto_scaling_obj.resource['CreationPolicy'] = creation_policy

        if update_policy is not None:
            auto_scaling_obj.resource['UpdatePolicy'] = update_policy

        if custom_tags != None and len(custom_tags) > 0:
            if type(custom_tags) != list:
                custom_tags = [custom_tags]
            auto_scaling_obj.Tags = custom_tags
        else:
            auto_scaling_obj.Tags = []

        auto_scaling_obj.Tags.append(autoscaling.Tag('Name', layer_name, True))
        return self.template.add_resource(auto_scaling_obj)

    def __init_region_map(self,
                          region_list):
        """
        Internal helper method used to check to ensure mapping dictionaries are present
        @param region_list [list(str)] array of strings representing the names of the regions to validate and/or create within the RegionMap CloudFormation mapping
        """
        if 'RegionMap' not in self.template.mappings:
            self.template.mappings['RegionMap'] = {}
        for region_name in region_list:
            if region_name not in self.template.mappings['RegionMap']:
                self.template.mappings['RegionMap'][region_name] = {}

    def create_reciprocal_sg(self,
                             source_group,
                             source_group_name,
                             destination_group,
                             destination_group_name,
                             from_port,
                             to_port=None,
                             ip_protocol='tcp'):
        """
        Helper method creates reciprocal ingress and egress rules given two existing security groups and a set of ports
        @param source_group [Troposphere.ec2.SecurityGroup] Object reference to the source security group
        @param source_group_name [string] friendly name of the source security group used for labels
        @param destination_group [Troposphere.ec2.SecurityGroup] Object reference to the destination security group
        @param destination_group_name [string] friendly name of the destination security group used for labels
        @param from_port [string] lower boundary of the port range to set for the secuirty group rules
        @param to_port [string] upper boundary of the port range to set for the security group rules
        @param ip_protocol [string] name of the IP protocol to set this rule for
        """
        if to_port == None:
            to_port = from_port
        if isinstance(from_port, unicode):
            from_port = from_port.encode('ascii', 'ignore')
        if isinstance(to_port, unicode):
            to_port = to_port.encode('ascii', 'ignore')
        if from_port == to_port:
            if isinstance(from_port, str):
                label_suffix = ip_protocol.capitalize() + from_port
            else:
                label_suffix = ip_protocol.capitalize() + 'Mapped'
        else:
            if isinstance(from_port, str) and isinstance(to_port, str):
                label_suffix = ip_protocol.capitalize() + from_port + 'To' + to_port
            else:
                label_suffix = ip_protocol.capitalize() + 'MappedPorts'

        CFN_TYPES = [GetAtt]

        if type(source_group) not in CFN_TYPES:
            source_group = Ref(source_group)

        if type(destination_group) not in CFN_TYPES:
            destination_group = Ref(destination_group)

        self.template.add_resource(ec2.SecurityGroupIngress(destination_group_name + 'Ingress' + source_group_name + label_suffix,
            SourceSecurityGroupId=source_group,
            GroupId=destination_group,
            FromPort=from_port,
            ToPort=to_port,
            IpProtocol=ip_protocol))

        self.template.add_resource(ec2.SecurityGroupEgress(source_group_name + 'Egress' + destination_group_name + label_suffix,
            DestinationSecurityGroupId=destination_group,
            GroupId=source_group,
            FromPort=from_port,
            ToPort=to_port,
            IpProtocol=ip_protocol))

    # Creates an ELB and attaches it to your template
    # Ports should be a dictionary of ELB ports to Instance ports
    # SSL cert name must be included if using ELB port 443
    # TODO: Parameterize more stuff
    def create_elb(self, resource_name, ports, utility_bucket=None, instances=[], security_groups=[], ssl_cert_name='', depends_on=[]):

        stickiness_policy_name = '%sElbStickinessPolicy' % resource_name
        stickiness_policy = elb.LBCookieStickinessPolicy(CookieExpirationPeriod='1800', PolicyName=stickiness_policy_name)
        
        listeners = []
        for elb_port in ports:
            if elb_port == tpc.HTTP_PORT:
                listeners.append(elb.Listener(LoadBalancerPort=elb_port, InstancePort=ports[elb_port], Protocol='HTTP', InstanceProtocol='HTTP',
                                 PolicyNames=[stickiness_policy_name]))
            elif elb_port == tpc.HTTPS_PORT:
                listeners.append(elb.Listener(LoadBalancerPort=elb_port, InstancePort=ports[elb_port], Protocol='HTTPS', InstanceProtocol='HTTPS',
                                 SSLCertificateId=Join("", ["arn:aws:iam::", {"Ref": "AWS::AccountId"}, ":server-certificate/", ssl_cert_name]),
                                 PolicyNames=[stickiness_policy_name]))
            else:
                listeners.append(elb.Listener(LoadBalancerPort=elb_port, InstancePort=ports[elb_port], Protocol='TCP', InstanceProtocol='TCP'))

        if tpc.HTTPS_PORT in ports:
            health_check_port = ports[tpc.HTTPS_PORT]
        elif tpc.HTTP_PORT in ports:
            health_check_port = ports[tpc.HTTP_PORT]
        else:
            health_check_port = ports.values()[0]

        elb_obj = elb.LoadBalancer(
            '%sElb' % resource_name,
            Subnets=self.subnets['public'],
            SecurityGroups=[Ref(sg) for sg in security_groups],
            CrossZone=True,
            LBCookieStickinessPolicy=[stickiness_policy],
            HealthCheck=elb.HealthCheck(
                HealthyThreshold=3,
                UnhealthyThreshold=5,
                Interval=30,
                Target='TCP:%s' % health_check_port,
                Timeout=5),
            Listeners=listeners,
            Instances=instances,
            Scheme='internet-facing',
            DependsOn=depends_on
        )

        if utility_bucket is not None:
            elb_obj.AccessLoggingPolicy = elb.AccessLoggingPolicy(
                EmitInterval=5,
                Enabled=True,
                S3BucketName=Ref(utility_bucket))

        return self.template.add_resource(elb_obj)

    def to_json(self):
        """
        Centralized method for managing outputting this template with a timestamp identifying when it was generated and for creating a SHA256 hash representing the template for validation purposes
        """
        return self.template.to_template_json()

    @staticmethod
    def build_bootstrap(bootstrap_files,
                        variable_declarations=None,
                        cleanup_commands=None,
                        prepend_line='#!/bin/bash'):
        """
        Method encapsulates process of building out the bootstrap given a set of variables and a bootstrap file to source from
        Returns base 64-wrapped, joined bootstrap to be applied to an instnace
        @param bootstrap_files [ string[] ] list of paths to the bash script(s) to read as the source for the bootstrap action to created
        @param variable_declaration [ list ] list of lines to add to the head of the file - used to inject bash variables into the script
        @param cleanup_commnds [ string[] ] list of lines to add at the end of the file - used for layer-specific details
        """
        if prepend_line != '':
            ret_val = [prepend_line]
        else:
            ret_val = []

        if variable_declarations != None:
            for line in variable_declarations:
                ret_val.append(line)
        for bootstrap_file in bootstrap_files:
            for line in EnvironmentBase.get_file_contents(bootstrap_file):
                ret_val.append(line)
        if cleanup_commands != None:
            for line in cleanup_commands:
                ret_val.append(line)
        return Base64(Join("\n", ret_val))

    @staticmethod
    def get_file_contents(file_name):
        """
        Method encpsulates reading a file into a list while removing newline characters
        @param file_name [string] path to file to read
        """
        ret_val = []
        with open(file_name) as f:
            content = f.readlines()
        for line in content:
            if not line.startswith('#~'):
                ret_val.append(line.replace("\n", ""))
        return ret_val

    def create_instance_profile(self,
                                layer_name,
                                iam_policies=None):
        """
        Helper method creates an IAM Role and Instance Profile for the optoinally specified IAM policies
        @param layer_name [string] friendly name for the Role and Instance Profile used for naming and path organization
        @param iam_policies [Troposphere.iam.Policy[]] array of IAM Policies to be associated with the Role and Instance Profile created
        """
        iam_role_obj = iam.Role(layer_name + 'IAMRole',
                AssumeRolePolicyDocument={
                    'Statement': [{
                        'Effect': 'Allow',
                        'Principal': {'Service': ['ec2.amazonaws.com']},
                        'Action': ['sts:AssumeRole']
                    }]},
                    Path=Join('',['/' + self.globals.get('environment_name', 'environmentbase') + '/', layer_name , '/']))

        if iam_policies != None:
            iam_role_obj.Policies = iam_policies

        iam_role = self.template.add_resource(iam_role_obj)

        return self.template.add_resource(iam.InstanceProfile(layer_name + 'InstancePolicy',
                Path='/' + self.globals.get('environment_name', 'environmentbase') + '/',
                Roles=[Ref(iam_role)]))

    def add_child_template(self,
                           name,
                           template_wrapper,
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
        template = template_wrapper.template

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

        stack_obj = cf.Stack(stack_name,
            TemplateURL=stack_url,
            Parameters=stack_params,
            TimeoutInMinutes=self.template_args.get('timeout_in_minutes', '60'),
            DependsOn=depends_on)

        return self.template.add_resource(stack_obj)

