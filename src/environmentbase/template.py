from troposphere import Output, Ref, Join, Parameter, Base64, GetAtt, FindInMap, Retain, Select, GetAZs
from troposphere import iam, ec2, autoscaling, route53 as r53, s3, logs, cloudwatch
from awacs import logs as awacs_logs, aws
from awacs.helpers.trust import make_simple_assume_statement
import troposphere as t
import troposphere.constants as tpc
import troposphere.elasticloadbalancing as elb
import troposphere.cloudformation as cf
import hashlib
import json
import os
import time
from datetime import datetime
import resources as res
import utility

from toolz.dicttoolz import merge


class Template(t.Template):
    """
    Custom wrapper for Troposphere Template object which handles S3 uploads and a specific
    workflow around hashing the template to allow for a validation mechanism of a template's
    consistency since it was generated.
    """

    # Class variable for S3 destination, set once by controller for use across all templates w/in an environment
    s3_path_prefix = ''

    # S3 bucket name used to store the templates
    template_bucket = ''

    # Timeout period after which to fail if a child stack has not reached a COMPLETE state
    stack_timeout = '60'

    def __init__(self, template_name):
        """
        Init method for environmentbase.Template class
        @param template_name [string] - name of this template, used to identify this template when uploading, deploying, etc.
        """
        t.Template.__init__(self)
        self.name = template_name
        self.AWSTemplateFormatVersion = ''

        self._vpc_cidr = None
        self._vpc_id = None
        self._common_security_group = None
        self._utility_bucket = None
        self._igw = None
        self._child_templates = []
        self._child_template_references = []
        self.manual_parameter_bindings = {}

        self._subnets = {}

    def _ref_maybe(self, item):
        """
        Wraps provided item in a troposphere.Ref() if the type makes sense to ref in cloudformation.
        This allows attributes to be saved w/o needing to ref() them before (or after).
        Note: Dicts and Lists are recursively processed for 'ref'able values
        """
        # Wrap input if type is:
        # - AWSDeclaration --> Parameters & Outputs
        # - AWSObject --> Resources
        # - items in a list or valued of a hash
        if isinstance(item, (t.AWSDeclaration, t.AWSObject)):
            return Ref(item)

        elif isinstance(item, list):
            items = []
            for i in item:
                items.append(self._ref_maybe(i))
            return items

        elif isinstance(item, dict):
            items = {}
            for (k, v) in item.iteritems():
                items.update({k: self._ref_maybe(v)})
            return items

        else:
            return item

    @property
    def vpc_cidr(self):
        return self._ref_maybe(self._vpc_cidr)

    @property
    def vpc_id(self):
        return self._ref_maybe(self._vpc_id)

    @property
    def common_security_group(self):
        return self._ref_maybe(self._common_security_group)

    @property
    def utility_bucket(self):
        return self._ref_maybe(self._utility_bucket)

    @property
    def igw(self):
        return self._ref_maybe(self._igw)

    @property
    def ec2_key(self):
        return self._ref_maybe(self._ec2_key)

    @property
    def vpc_gateway_attachment(self):
        return self._ref_maybe(self._vpc_gateway_attachment)

    @property
    def subnets(self):
        return self._ref_maybe(self._subnets)

    def __get_template_hash(self):
        """
        Private method holds process for hashing this template for future validation.
        """
        m = hashlib.sha256()
        m.update(self.__validation_formatter())
        return m.hexdigest()

    def merge(self, other_template):
        """
        Experimental merge function
        1. This passes all the initialized attributes to the other template
        2. Calls the other template's build_hook()
        3. Copies the generated troposphere attributes back into this template

        NOTE: This function is currently used successfully in networkbase for merging the HaNat pattern
              into the root template. It has not been thoroughly tested in more complex scenarios
        """
        other_template.copy_attributes_from(self)

        other_template.build_hook()

        self.metadata.update(other_template.metadata)
        self.conditions.update(other_template.conditions)
        self.mappings.update(other_template.mappings)
        self.outputs.update(other_template.outputs)
        self.parameters.update(other_template.parameters)
        self.resources.update(other_template.resources)

    def copy_attributes_from(self, other_template):
        """
        Copies all attributes from the other template into this one
        These typically get initialized for a template when add_child_template is called
        from the controller, but that never happens when merging two templates
        """
        self._vpc_cidr               = other_template._vpc_cidr
        self._vpc_id                 = other_template._vpc_id
        self._common_security_group  = other_template._common_security_group
        self._utility_bucket         = other_template._utility_bucket
        self._igw                    = other_template._igw
        self._vpc_gateway_attachment = other_template._vpc_gateway_attachment

        self._subnets    = other_template.subnets.copy()

        self.parameters = other_template.parameters.copy()
        self.mappings   = other_template.mappings.copy()
        self.metadata   = other_template.metadata.copy()
        self.conditions = other_template.conditions.copy()
        self.outputs    = other_template.outputs.copy()
        self.resources  = other_template.resources.copy()

    def build_hook(self):
        """
        Provides template subclasses a place to assemble resources with access to common parameters and mappings.
        Executed by add_child_template() after add_common_params_to_child_template() and load_ami_cache()
        """
        pass

    @staticmethod
    def get_config_schema():
        """
        This method is provided for subclasses to update config requirements with additional required keys and their types.
        If you define both this and the get_factory_defaults() functions in your template and register the template with
        the controller's config handlers, these values will be included when you run the `init` command

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
    def get_factory_defaults():
        """
        This method is provided for subclasses to update factory default config file with additional sections.
        If you define both this and the get_config_schema() functions in your template and register the template with
        the controller's config handlers, these values will be included when you run the `init` command

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

    def to_template_json(self):
        """
        Process all child templates recursively and render this template as json with a timestamp identifying
        when it was generated along with a SHA256 hash representing the template for validation purposes
        """
        self.process_child_templates()

        # strip existing values
        for output_key in ['dateGenerated', 'templateValidationHash']:
            if output_key in self.outputs:
                self.outputs.pop(output_key)

        # set the date that this template was generated
        if 'dateGenerated' not in self.outputs:
            self.add_output(Output(
                'dateGenerated',
                Value=str(datetime.utcnow()),
                Description='UTC datetime representation of when this template was generated'))

        # generate the template validation hash
        if 'templateValidationHash' not in self.outputs:
            self.add_output(Output(
                'templateValidationHash',
                Value=self.__get_template_hash(),
                Description='Hash of this template that can be used as a simple means of validating whether a template has been changed since it was generated.'))

        return self.to_json()

    def validate_template(self):
        """
        Centralized method for validating this templates' templateValidationHash value
        """
        if 'templateValidationHash' not in self.outputs:
            raise ValueError('This template does not contain a templateValidationHash output value')
        else:
            output_value = self.outputs.pop('templateValidationHash')
            computed_hash = self.__get_template_hash()
            if output_value.Value != computed_hash:
                raise ValueError('Template failed validation check. Template hash is [' + output_value.get('Value') + '] and computed hash is [' + computed_hash + ']')
            else:
                return True

    def __validation_formatter(self):
        """
        Validation formatter helps to ensure consistent formatting for hash validation workflow
        """
        return json.dumps(json.loads(self.to_json()), separators=(',', ':'))

    def add_parameter_idempotent(self, troposphere_parameter):
        """
        Idempotent add (add only if not exists) for parameters within the template
        @param [Troposphere.Parameter] Troposphere Parameter to add to this template
        """
        if troposphere_parameter.title not in self.parameters:
            return self.add_parameter(troposphere_parameter)
        else:
            return None

    def add_instance_profile(self, layer_name, iam_policies, path_prefix):
        """
        Helper function to add role and instance profile resources to this template
        using the provided iam_policies. The instance_profile will be created at:
        '/<path_prefix>/<layer_name>/'
        """
        iam_role_obj = iam.Role(layer_name + 'IAMRole',
                AssumeRolePolicyDocument={
                    'Statement': [{
                        'Effect': 'Allow',
                        'Principal': {'Service': ['ec2.amazonaws.com']},
                        'Action': ['sts:AssumeRole']
                    }]},
                    Path=Join('', ['/' + path_prefix + '/', layer_name , '/']))

        if iam_policies != None:
            iam_role_obj.Policies = iam_policies

        iam_role = self.add_resource(iam_role_obj)

        return self.add_resource(iam.InstanceProfile(layer_name + 'InstancePolicy',
                Path='/' + path_prefix + '/',
                Roles=[Ref(iam_role)]))

    def add_common_parameters_from_parent(self, parent):
        ec2_key = parent._ec2_key.Default
        parent_subnets = parent._subnets if not self._subnets else {}

        if 'RegionMap' in self.mappings:
            region_map = dict(self._merge_region_map(self.mappings['RegionMap'], parent.mappings['RegionMap']))
        else:
            region_map = parent.mappings['RegionMap']
        self.add_common_parameters(ec2_key, region_map, parent_subnets)

    def _merge_region_map(self, map1, map2):
        for key in set(map1.keys() + map2.keys()):
            yield (key, merge(map1[key], map2[key]))

    def add_common_parameters(self, ec2_key, region_map, parent_subnets):
        """
        Adds the common set of parameters that are available to every child template
        The values are automatically matched from the root template
            vpcCidr,
            vpcId,
            commonSecurityGroup,
            utilityBucket,
            each subnet: [public|private]Subnet[0-9]
        """
        self._vpc_cidr = self.add_parameter(Parameter(
            'vpcCidr',
            Description='CIDR of the VPC network',
            Type='String',
            AllowedPattern=res.get_str('cidr_regex'),
            ConstraintDescription=res.get_str('cidr_regex_message')))

        self._vpc_id = self.add_parameter(Parameter(
            'vpcId',
            Description='ID of the VPC network',
            Type='String'))

        self._common_security_group = self.add_parameter(Parameter(
            'commonSecurityGroup',
            Description='Security Group ID of the common security group for this environment',
            Type='String'))

        self._utility_bucket = self.add_parameter(Parameter(
            'utilityBucket',
            Description='Name of the S3 bucket used for infrastructure utility',
            Type='String'))

        self._igw = self.add_parameter(Parameter(
            'internetGateway',
            Description='Name of the internet gateway used by the vpc',
            Type='String'))

        self._vpc_gateway_attachment = self.add_parameter(Parameter(
            'igwVpcAttachment',
            Description='VPCGatewayAttachment for the VPC and IGW',
            Type='String'))

        self._ec2_key = self.add_parameter(Parameter(
           'ec2Key',
            Type='String',
            Default=ec2_key,
            Description='Name of an existing EC2 KeyPair to enable SSH access to the instances',
            AllowedPattern=res.get_str('ec2_key'),
            MinLength=1,
            MaxLength=255,
            ConstraintDescription=res.get_str('ec2_key_message')
        ))

        self.mappings['RegionMap'] = region_map

        for subnet_type in parent_subnets:
            if subnet_type not in self._subnets:
                self._subnets[subnet_type] = {}

            for subnet_layer in parent_subnets[subnet_type]:
                if subnet_layer not in self._subnets[subnet_type]:
                    self._subnets[subnet_type][subnet_layer] = []

                for subnet in parent_subnets[subnet_type][subnet_layer]:
                    if isinstance(subnet, Parameter):
                        subnet_name = subnet.title
                    else:
                        subnet_name = subnet.data['Ref']
                    self._subnets[subnet_type][subnet_layer].append(self.add_parameter(Parameter(
                        subnet_name,
                        Description=subnet_name,
                        Type='String')))


    @staticmethod
    def construct_user_data(env_vars={}, user_data=''):
        """
        Wrapper method to encapsulate process of constructing userdata for a launch configuration
        @param env_vars [dict] A dictionary containining key value pairs to set as environment variables in the userdata
        @param user_data [string] Contents of the user data script as a string
        Returns user_data_payload [string[]] Userdata payload ready to be dropped into a launch configuration
        """
        # At least one of env_vars or user_data must exist
        if not (env_vars or user_data):
            return []

        # If the variable value is not a string, use the Join function
        # This handles Refs, Parameters, etc. which are evaluated at runtime
        variable_declarations = []
        for k,v in env_vars.iteritems():
            if isinstance(v, basestring):
                variable_declarations.append('%s=%s' % (k, v))
            else:
                variable_declarations.append(Join('=', [k, v]))

        return Template.build_bootstrap(
            bootstrap_files=[user_data],
            variable_declarations=variable_declarations
        )


    @staticmethod
    def build_bootstrap(bootstrap_files=None,
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

        if variable_declarations is not None:
            for line in variable_declarations:
                ret_val.append(line)
        for file_name_or_content in bootstrap_files:
            for line in Template.get_file_contents(file_name_or_content):
                ret_val.append(line)
        if cleanup_commands is not None:
            for line in cleanup_commands:
                ret_val.append(line)
        return Base64(Join("\n", ret_val))

    @staticmethod
    def get_file_contents(file_name_or_content):
        """
        Method encpsulates reading a file into a list while removing newline characters.
        If file is not found the variable is interpreted as the file content itself.
        @param file_name_or_content [string] path to file to read or content itself
        """
        ret_val = []
        if not os.path.isfile(file_name_or_content):
            content = file_name_or_content.split('\n')
        else:
            with open(file_name_or_content) as f:
                content = f.readlines()

        for line in content:
            if not line.startswith('#~'):
                ret_val.append(line.replace("\n", ""))
        return ret_val

    def add_ami_mapping(self, json_data):
        """
        Method gets the ami cache from the file locally and adds a mapping for ami ids per region into the template
        This depends on populating ami_cache.json with the AMI ids that are output by the packer scripts per region
        @param ami_map_file [string] path representing where to find the AMI map to ingest into this template
        """
        for region in json_data:
            for key in json_data[region]:
                self.add_region_map_value(region, key, json_data[region][key])

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
        if region not in self.mappings['RegionMap']:
            self.mappings['RegionMap'][region] = {}
        self.mappings['RegionMap'][region][key] = value

    def __init_region_map(self,
                          region_list):
        """
        Internal helper method used to check to ensure mapping dictionaries are present
        @param region_list [list(str)] array of strings representing the names of the regions to validate and/or create within the RegionMap CloudFormation mapping
        """
        if 'RegionMap' not in self.mappings:
            self.mappings['RegionMap'] = {}
        for region_name in region_list:
            if region_name not in self.mappings['RegionMap']:
                self.mappings['RegionMap'][region_name] = {}

    def add_scaling_policy(self,
        metric_name,
        asg_name,
        adjustment_type="ChangeInCapacity",
        cooldown=1,
        scaling_adjustment=1):
        """
        Helper method to encapsulate process of adding a scaling policy to an autoscaling group in this template
        """
        policy = autoscaling.ScalingPolicy(
            metric_name + 'ScalingPolicy',
            AdjustmentType=adjustment_type,
            AutoScalingGroupName=Ref(asg_name),
            Cooldown=cooldown,
            ScalingAdjustment=str(scaling_adjustment))

        return self.add_resource(policy)

    def add_cloudwatch_alarm(self,
        layer_name,
        scaling_policy_name,
        asg_name,
        metric_name='CPUUtilization',
        evaluation_periods=1,
        statistic='Average',
        threshold=10,
        period=60,
        namespace='AWS/EC2',
        comparison_operator='GreaterThanThreshold'):
        """
        Helper method to encapsulate process of adding a cloudwatch alarm resource to a scaling policy
        """
        alarm = cloudwatch.Alarm(
            layer_name + 'Alarm',
            EvaluationPeriods=str(evaluation_periods),
            Statistic=statistic,
            Threshold=str(threshold),
            Period=str(period),
            AlarmActions=[Ref(scaling_policy_name)],
            Namespace=namespace,
            Dimensions=[cloudwatch.MetricDimension(Name="AutoScalingGroupName",Value=Ref(asg_name))],
            ComparisonOperator=comparison_operator,
            MetricName=metric_name)

        return self.add_resource(alarm)

    def add_asg(self,
                layer_name,
                instance_profile=None,
                instance_type='t2.micro',
                ami_name='amazonLinuxAmiId',
                ec2_key=None,
                user_data=None,
                security_groups=None,
                min_size=1,
                max_size=1,
                root_volume_size=None,
                root_volume_type=None,
                include_ephemerals=True,
                number_ephemeral_vols=2,
                ebs_data_volumes=None,  # [{'size':'100', 'type':'gp2', 'delete_on_termination': True, 'iops': 4000, 'volume_type': 'io1'}],
                scaling_policies=None,  # [{'metric_name':'MyCustomMetric', 'comparison_operator':'GreaterThanThreshold', threshold':10, scaling_adjustment: 1}]
                custom_tags=None,
                load_balancer=None,
                health_check_type='EC2',
                health_check_grace_period=0,
                instance_monitoring=False,
                subnet_layer=None,
                associate_public_ip=None,
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
        @param security_groups [Troposphere.ec2.SecurityGroup[]] array of security groups to be applied to instances within this Auto Scaling group
        @param min_size [int] value to set as the minimum number of instances for the Auto Scaling group
        @param max_size [int] value to set as the maximum number of instances for the Auto Scaling group
        @param root_volume_size [int] size (in GiB) to assign to the root volume of the launched instance
        @param include_ephemerals [Boolean] indicates that ephemeral volumes should be included in the block device mapping of the Launch Configuration
        @param number_ephemeral_vols [int] number of ephemeral volumes to attach within the block device mapping Launch Configuration
        @param ebs_data_volumes [list] dictionary pair of size and type data properties in a list used to create ebs volume attachments
        @param scaling_policies [list] dictionaries describing scaling policies and their associated cloudwatch alarms
        @param custom_tags [Troposphere.autoscaling.Tag[]] Collection of Auto Scaling tags to be assigned to the Auto Scaling Group
        @param load_balancer [Troposphere.elasticloadbalancing.LoadBalancer] Object reference to an ELB to be assigned to this auto scaling group
        @param instance_monitoring [Boolean] indicates that detailed monitoring should be turned on for all instnaces launched within this Auto Scaling group
        @param subnet_layer [string] string indicating which subnet layer instances are being launched into
        """

        # Ensure that all the passed in parameters are Ref objects
        if ec2_key and type(ec2_key) != Ref:
            ec2_key = Ref(ec2_key)
        elif ec2_key is None:
            ec2_key = Ref(self.parameters['ec2Key'])

        if type(instance_type) != str:
            instance_type = Ref(instance_type)

        sg_list = []
        for sg in security_groups:
            if isinstance(sg, Ref):
                sg_list.append(sg)
            else:
                sg_list.append(Ref(sg))

        # If no instance profile was provided, create one with just the cloudformation read policy
        if not instance_profile:
            instance_profile = self.add_instance_profile(layer_name, [self.get_cfn_policy()], self.name)

        # If subnet_layer isn't passed in, try a private subnet if available, else a public subnet
        if not subnet_layer:
            if len(self._subnets.get('private')) > 0:
                subnet_layer = self._subnets['private'].keys()[0]
            else:
                subnet_layer = self._subnets['public'].keys()[0]

        subnet_type = self.get_subnet_type(subnet_layer)

        # If associate_public_ip is not passed in, set it based on the subnet_type
        if not associate_public_ip:
            associate_public_ip = True if subnet_type == 'public' else False

        launch_config_obj = autoscaling.LaunchConfiguration(
            layer_name + 'LaunchConfiguration',
            IamInstanceProfile=Ref(instance_profile),
            ImageId=FindInMap('RegionMap', Ref('AWS::Region'), ami_name),
            InstanceType=instance_type,
            SecurityGroups=sg_list,
            KeyName=ec2_key,
            AssociatePublicIpAddress=associate_public_ip,
            InstanceMonitoring=instance_monitoring)

        if launch_config_metadata:
            launch_config_obj.Metadata = launch_config_metadata

        if user_data:
            launch_config_obj.UserData = user_data

        block_devices = []
        if root_volume_type and root_volume_size:
            ebs_device = ec2.EBSBlockDevice(
                VolumeSize=root_volume_size)

            if root_volume_type:
                ebs_device.VolumeType = root_volume_type

            block_devices.append(ec2.BlockDeviceMapping(
                DeviceName='/dev/sda1',
                Ebs=ebs_device))

        device_names = ['/dev/sd%s' % c for c in 'bcdefghijklmnopqrstuvwxyz']

        if ebs_data_volumes is not None and len(ebs_data_volumes) > 0:
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
                    DeviceName=device_name,
                    Ebs=ebs_block_device))

        if include_ephemerals and number_ephemeral_vols > 0:
            device_names.reverse()
            for x in range(0, number_ephemeral_vols):
                device_name = device_names.pop()
                block_devices.append(ec2.BlockDeviceMapping(
                    DeviceName=device_name,
                    VirtualName='ephemeral' + str(x)))

        if len(block_devices) > 0:
            launch_config_obj.BlockDeviceMappings = block_devices

        launch_config = self.add_resource(launch_config_obj)

        auto_scaling_obj = autoscaling.AutoScalingGroup(
            layer_name + 'AutoScalingGroup',
            AvailabilityZones=GetAZs(),
            LaunchConfigurationName=Ref(launch_config),
            MaxSize=max_size,
            MinSize=min_size,
            DesiredCapacity=min(min_size, max_size),
            VPCZoneIdentifier=self.subnets[subnet_type][subnet_layer.lower()],
            TerminationPolicies=['OldestLaunchConfiguration', 'ClosestToNextInstanceHour', 'Default'],
            DependsOn=depends_on,
            HealthCheckGracePeriod=health_check_grace_period,
            HealthCheckType=health_check_type)

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

        if custom_tags is not None and len(custom_tags) > 0:
            if type(custom_tags) != list:
                custom_tags = [custom_tags]
            auto_scaling_obj.Tags = custom_tags
        else:
            auto_scaling_obj.Tags = []

        if scaling_policies is not None and len(scaling_policies) > 0:
            for scaling_policy in scaling_policies:
                policy_obj = self.add_scaling_policy(
                    scaling_policy.get('metric_name'),
                    auto_scaling_obj.name,
                    scaling_adjustment=scaling_policy.get('scaling_adjustment'),
                    adjustment_type=scaling_policy.get('adjustment_type','ChangeInCapacity'),
                    cooldown=scaling_policy.get('cooldown',1))

                self.add_cloudwatch_alarm(scaling_policy.get('metric_name'),
                    policy_obj.name,
                    auto_scaling_obj.name,
                    metric_name=scaling_policy.get('metric_name'),
                    threshold=scaling_policy.get('threshold'),
                    comparison_operator=scaling_policy.get('comparison_operator','GreaterThanThreshold'),
                    evaluation_periods=scaling_policy.get('evaluation_periods',1),
                    statistic=scaling_policy.get('statistic', 'Average'),
                    period=scaling_policy.get('period', 60),
                    namespace=scaling_policy.get('namespace','AWS/EC2'),
                    )

        auto_scaling_obj.Tags.append(autoscaling.Tag('Name', layer_name, True))
        return self.add_resource(auto_scaling_obj)

    def add_elb(self, resource_name, listeners, utility_bucket=None, instances=[], security_groups=[], depends_on=[], subnet_layer=None, scheme='internet-facing', health_check_protocol='TCP', health_check_port=None, health_check_path='', idle_timeout=None):
        """
        Helper function creates an ELB and attaches it to your template
        Listeners should be a list of dictionaries, each containining:
            elb_port - The port of the incoming connection to the ELB
            elb_protocol (optional) - The protocol of the incoming connection to the ELB (i.e., 'HTTP', 'HTTPS', 'TCP', 'SSL')
            instance_port (optional) - The port of the incoming connection to the instances
            instance_protocol (optional) - The protocol of the incoming connection to the instances (i.e., 'HTTP', 'HTTPS', 'TCP', 'SSL')
            ssl_cert_name (optional) - The name of the SSL cert in IAM (Only required if the elb_protocol is 'HTTPS' or 'SSL')
        """
        # Create default session stickiness policy
        # TODO: Parameterize the policy configuration (per listener?)
        stickiness_policy_name = '%sElbStickinessPolicy' % resource_name
        stickiness_policy = elb.LBCookieStickinessPolicy(CookieExpirationPeriod='1800', PolicyName=stickiness_policy_name)

        # Construct the listener objects based on the passed in listeners dictionary
        elb_listeners = []
        for listener in listeners:

            elb_port = listener.get('elb_port')
            elb_protocol = listener.get('elb_protocol').upper() if listener.get('elb_protocol') else 'TCP'

            # If back end parameters are not specified, use the values for the front end
            instance_port = listener.get('instance_port') if listener.get('instance_port') else elb_port
            instance_protocol = listener.get('instance_protocol').upper() if listener.get('instance_protocol') else elb_protocol

            elb_listener = elb.Listener(Protocol=elb_protocol,
                                        LoadBalancerPort=elb_port,
                                        InstanceProtocol=instance_protocol,
                                        InstancePort=instance_port)

            # SSL Cert must be included if using either SSL or HTTPS for elb_protocol
            ssl_cert_name = listener.get('ssl_cert_name')
            if ssl_cert_name:
                elb_listener.SSLCertificateId = Join("", ["arn:aws:iam::", {"Ref": "AWS::AccountId"}, ":server-certificate/", ssl_cert_name])

            # Create the default session stickiness policy for HTTP or HTTPS listeners
            # TODO: Parameterize whether or not to include this policy
            if elb_protocol == 'HTTP' or elb_protocol == 'HTTPS':
                elb_listener.PolicyNames = [stickiness_policy_name]

            elb_listeners.append(elb_listener)

        if subnet_layer:
            subnet_type = self.get_subnet_type(subnet_layer)
        else:
            # If subnet layer is not passed in, determine based on the scheme
            # -- Pick a public subnet if it's internet-facing, else pick a private one
            subnet_type = 'public' if scheme == 'internet-facing' else 'private'
            subnet_layer = self._subnets[subnet_type].keys()[0]

        elb_obj = elb.LoadBalancer(
            '%sElb' % resource_name,
            Subnets=self.subnets[subnet_type][subnet_layer],
            SecurityGroups=[Ref(sg) for sg in security_groups],
            CrossZone=True,
            LBCookieStickinessPolicy=[stickiness_policy],
            Listeners=elb_listeners,
            Instances=instances,
            Scheme=scheme,
            DependsOn=depends_on
        )

        # If the health_check_port was not specified, use the instance port of any of the listeners (elb_port is used if instance_port isn't set)
        if not health_check_port:
            health_check_port = [listener.get('instance_port') if listener.get('instance_port') else listener.get('elb_port') for listener in listeners][0]


        # Construct the ELB Health Check target based on the passed in health_check_protocol and health_check_port parameters
        health_check_protocol = health_check_protocol.upper()
        health_check_target = "%s:%s" % (health_check_protocol, health_check_port)

        # Add the health check path for HTTP/S targets (i.e., '/version', '/about', etc.)
        if health_check_protocol == 'HTTP' or health_check_protocol == 'HTTPS':
            # Ensure exactly one '/' at the beginning of the path string
            health_check_target += "/%s" % health_check_path.lstrip('/')

        # TODO: Parameterize this stuff
        elb_obj.HealthCheck = elb.HealthCheck(
            HealthyThreshold=3,
            UnhealthyThreshold=5,
            Interval=30,
            Target=health_check_target,
            Timeout=5
        )

        # If an S3 utility bucket was passed in, set up the ELB access log
        if utility_bucket is not None:
            elb_obj.AccessLoggingPolicy = elb.AccessLoggingPolicy(
                EmitInterval=5,
                Enabled=True,
                S3BucketName=utility_bucket)

        # If the idle_timeout was passed in, create a ConnectionSettings object with the Idle Timeout
        if idle_timeout:
            elb_obj.ConnectionSettings = elb.ConnectionSettings(IdleTimeout=idle_timeout)

        return self.add_resource(elb_obj)

    def create_reciprocal_sg(self,
                             source_group,
                             source_group_name,
                             destination_group,
                             destination_group_name,
                             from_port,
                             to_port=None,
                             ip_protocol='tcp'):
        """
        Helper method creates reciprocal ingress and egress rules given two existing security groups and a range of ports
        @param source_group [Troposphere.ec2.SecurityGroup] Object reference to the source security group
        @param source_group_name [string] friendly name of the source security group used for labels
        @param destination_group [Troposphere.ec2.SecurityGroup] Object reference to the destination security group
        @param destination_group_name [string] friendly name of the destination security group used for labels
        @param from_port [string] lower boundary of the port range to set for the secuirty group rules
        @param to_port [string] upper boundary of the port range to set for the security group rules
        @param ip_protocol [string] name of the IP protocol to set this rule for
        """
        if to_port is None:
            to_port = from_port
        if isinstance(from_port, unicode):
            from_port = from_port.encode('ascii', 'ignore')
        if isinstance(to_port, unicode):
            to_port = to_port.encode('ascii', 'ignore')
        if from_port == to_port:
            label_suffix = ip_protocol.capitalize() + str(from_port)
        else:
            label_suffix = ip_protocol.capitalize() + str(from_port) + 'To' + str(to_port)

        # A Ref cannot be created from an object that is already a GetAtt
        # and possibly some other CFN types, so expand this list if you discover another one
        CFN_TYPES = [GetAtt]
        if type(source_group) not in CFN_TYPES:
            source_group = Ref(source_group)
        if type(destination_group) not in CFN_TYPES:
            destination_group = Ref(destination_group)

        self.add_resource(ec2.SecurityGroupIngress(
            destination_group_name + 'Ingress' + source_group_name + label_suffix,
            SourceSecurityGroupId=source_group,
            GroupId=destination_group,
            FromPort=from_port,
            ToPort=to_port,
            IpProtocol=ip_protocol))

        self.add_resource(ec2.SecurityGroupEgress(
            source_group_name + 'Egress' + destination_group_name + label_suffix,
            DestinationSecurityGroupId=destination_group,
            GroupId=source_group,
            FromPort=from_port,
            ToPort=to_port,
            IpProtocol=ip_protocol))

    def get_cfn_policy(self):
        """
        Helper method returns the standard IAM policy to allow cloudformation read actions
        """
        return iam.Policy(
            PolicyName='cloudformationRead',
            PolicyDocument={
                "Statement": [{
                    "Effect": "Allow",
                    "Action": [
                        "cloudformation:DescribeStackEvents",
                        "cloudformation:DescribeStackResource",
                        "cloudformation:DescribeStackResources",
                        "cloudformation:DescribeStacks",
                        "cloudformation:ListStacks",
                        "cloudformation:ListStackResources"],
                    "Resource": "*"}]
            })

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
        if 'environmentHostedZone' not in self.parameters:
            hostedzone = self.add_parameter(Parameter(
                "environmentHostedZone",
                Description="The DNS name of an existing Amazon Route 53 hosted zone",
                Default=tier_args.get('base_hosted_zone_name', 'devopsdemo.com'),
                Type="String"))
        else:
            hostedzone = self.parameters.get('environmentHostedZone')

        if tier_name.lower() + 'HostName' not in self.parameters:
            host_name = self.add_parameter(Parameter(
                tier_name.lower() + 'HostName',
                Description="Friendly host name to append to the environmentHostedZone base DNS record",
                Type="String",
                Default=tier_args.get('tier_host_name', tier_name.lower())))
        else:
            host_name = self.parameters.get(tier_name.lower() + 'HostName')

        self.add_resource(r53.RecordSetType(
            tier_name.lower() + 'DnsRecord',
            HostedZoneName=Join('', [Ref(hostedzone), '.']),
            Comment='CNAME record for ' + tier_name.capitalize() + ' tier',
            Name=Join('', [Ref(host_name), '.', Ref(hostedzone)]),
            Type='CNAME',
            TTL='300',
            ResourceRecords=[GetAtt(elb, 'DNSName')]))

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

        # The principal account IDs in the following statements refer to the AWS CloudTrail account IDs
        # They explicitly need write permissions in order to upload logs to your bucket
        statements = [{
            "Action": ["s3:PutObject"],
            "Effect": "Allow",
            "Resource": Join('', ['arn:aws:s3:::', utility_bucket, '/', elb_log_prefix + 'AWSLogs/', Ref('AWS::AccountId'), '/*']),
            "Principal": {"AWS": [FindInMap('RegionMap', Ref('AWS::Region'), 'elbAccountId')]}},
            {
                "Action": ["s3:GetBucketAcl"],
                "Resource": Join('', ["arn:aws:s3:::", utility_bucket]),
                "Effect": "Allow",
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
            {
                "Action": ["s3:PutObject"],
                "Resource": Join('', ["arn:aws:s3:::", utility_bucket, '/', cloudtrail_log_prefix + "AWSLogs/", Ref("AWS::AccountId"), '/*']),
                "Effect": "Allow",
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
                    "Condition": {"StringEquals": {"s3:x-amz-acl": "bucket-owner-full-control"}}}]

        self.add_output(Output(
            'elbAccessLoggingBucketAndPath',
            Value=Join('', ['arn:aws:s3:::', utility_bucket, elb_log_prefix]),
            Description='S3 bucket and key name prefix to use when configuring elb access logs to aggregate to S3'))

        self.add_output(Output(
            'cloudTrailLoggingBucketAndPath',
            Value=Join('', ['arn:aws:s3:::', utility_bucket, cloudtrail_log_prefix]),
            Description='S3 bucket and key name prefix to use when configuring CloudTrail to aggregate logs to S3'))

        return {"Statement": statements}

    def create_vpcflowlogs_role(self):
        flowlogs_policy = aws.Policy(
            Version="2012-10-17",
            Statement=[
                aws.Statement(
                    Sid="",
                    Effect=aws.Allow,
                    Resource=['*'],
                    Action=[awacs_logs.CreateLogGroup,
                            awacs_logs.CreateLogStream,
                            awacs_logs.PutLogEvents,
                            awacs_logs.DescribeLogGroups,
                            awacs_logs.DescribeLogStreams],
                )
            ]
        )

        flowlogs_trust_policy = aws.Policy(
            Version="2012-10-17",
            Statement=[make_simple_assume_statement("vpc-flow-logs.amazonaws.com")]
        )

        vpcflowlogs_role = iam.Role(
            'VPCFlowLogsIAMRole',
            AssumeRolePolicyDocument=flowlogs_trust_policy,
            Path='/',
            Policies=[
                iam.Policy(PolicyName='vpcflowlogs_policy', PolicyDocument=flowlogs_policy)
            ])

        return vpcflowlogs_role

    def add_utility_bucket(self, name=None):
        """
        Method adds a bucket to be used for infrastructure utility purposes such as backups
        @param name [str] friendly name to prepend to the CloudFormation asset name
        """
        if name:
            self._utility_bucket = name
        else:
            self._utility_bucket = self.add_resource(s3.Bucket(
                name.lower() + 'UtilityBucket',
                AccessControl=s3.BucketOwnerFullControl,
                DeletionPolicy=Retain))

            bucket_policy_statements = self.get_logging_bucket_policy_document(
                self.utility_bucket,
                elb_log_prefix=res.get_str('elb_log_prefix', ''),
                cloudtrail_log_prefix=res.get_str('cloudtrail_log_prefix', ''))

            self.add_resource(s3.BucketPolicy(
                name.lower() + 'UtilityBucketLoggingPolicy',
                Bucket=self.utility_bucket,
                PolicyDocument=bucket_policy_statements))

        log_group_name = 'DefaultLogGroup'
        self.add_resource(logs.LogGroup(
            log_group_name,
            RetentionInDays=7
        ))

        self.add_resource(self.create_vpcflowlogs_role())

        self.manual_parameter_bindings['utilityBucket'] = self.utility_bucket

    def add_child_template(self, child_template, merge=False, depends_on=[], output_autowire=True, propagate_outputs=True):
        """
        Appends the template to a list of child templates nested under this one
        These will be processed all together at the end of the create process in process_child_templates()
        """
        child_template_entry = (child_template, merge, depends_on, output_autowire, propagate_outputs)
        self._child_templates.append(child_template_entry)
        return child_template

    def add_child_template_reference(self, template_name, template_url, stack_params={}, depends_on=[]):
        """
        Create a child stack from a template that is not defined in this environment
        Useful for B/G deployments
        NOTE: If the original stack was deployed with any parameters, you must provide stack_params with
              this deployment. You can use utility.get_stack_params_from_parent_template() to
              retrieve the parameters that it was originally deployed with
        """
        return self.add_stack(
            template_name=template_name,
            template_url=template_url,
            stack_params=stack_params,
            depends_on=depends_on)

    def process_child_templates(self):
        """
        Iterate through and process the generated child template list
        """
        for (child_template, merge, depends_on, output_autowire, propagate_outputs) in self._child_templates:
            self.process_child_template(child_template, merge, depends_on, output_autowire, propagate_outputs)

    def process_child_template(self, child_template, merge, depends_on, output_autowire=True, propagate_outputs=True):
        """
        Add the common parameters from this template to the child template
        Execute the child template's build hook function
        Get the matching stack parameter values from this template and add the
        stack reference to this template with those stack parameters
        """

        # This merges all attributes from the two stacks together, so all this parameter binding is unnecessary
        if merge:
            self.merge(child_template)
            return

        # Add parameters from parent stack before executing build_hook
        child_template.add_common_parameters_from_parent(self)
        child_template.build_hook()
        if output_autowire:
            self.add_child_outputs_to_parameter_binding(child_template, propagate_up=propagate_outputs)

        # Match the stack parameters with parent stack parameter values and manual parameter bindings
        stack_params = self.match_stack_parameters(child_template)

        # Construct the resource path based on the prefix + name + timestamp
        child_template.resource_path = utility.get_template_s3_resource_path(
            prefix=Template.s3_path_prefix,
            template_name=child_template.name,
            include_timestamp=Template.include_timestamp)

        # Construct the template url using the bucket name and resource path
        template_s3_url = utility.get_template_s3_url(Template.template_bucket, child_template.resource_path)

        # Create the stack resource in this template and return the reference
        return self.add_stack(
            template_name=child_template.name,
            template_url=template_s3_url,
            stack_params=stack_params,
            depends_on=depends_on)

    def add_stack(self, template_name, template_url, stack_params={}, depends_on=[]):
        """
        Creates a cloudformation stack resource in this template with the attributes provided
        """
        stack_obj = cf.Stack(
            template_name,
            TemplateURL=template_url,
            Parameters=stack_params,
            TimeoutInMinutes=Template.stack_timeout,
            DependsOn=depends_on)

        return self.add_resource(stack_obj)

    def add_child_outputs_to_parameter_binding(self, child_template, propagate_up=False):
        """
        This auto-wires the outputs of the child stack to the manual_param of the parent stack
        """
        for output in child_template.outputs:
            self.manual_parameter_bindings[output] = GetAtt(child_template.name, "Outputs." + output)
            if propagate_up:
                self.add_output(Output(output, Value=GetAtt(child_template.name, "Outputs." + output)))
            # TODO: should a custom resource be addeded for each output?

    def match_stack_parameters(self, child_template):
        """
        For all matching parameters between this template and the child template, attempt to
        get the value from this template's parameters, resources, and manual parameter bindings.
        Return the dictionary of stack parameters to deploy the child template with
        """
        stack_params = {}

        for parameter in child_template.parameters.keys():

            # Manual parameter bindings single-namespace
            if parameter in self.manual_parameter_bindings:
                manual_match = self.manual_parameter_bindings[parameter]
                stack_params[parameter] = manual_match

            # Match any child stack parameters that have the same name as this stacks **parameters**
            elif parameter in self.parameters.keys():
                param_match = self.parameters.get(parameter)
                stack_params[parameter] = Ref(param_match)

            # Match any child stack parameters that have the same name as this stacks **resources**
            elif parameter in self.resources.keys():
                resource_match = self.resources.get(parameter)
                stack_params[parameter] = Ref(resource_match)

            # # Match any child stack parameters that have the same name as a top-level **stack_output**
            # TODO: Enable Output autowiring
            # elif parameter in self.stack_outputs:
            #     stack_params[parameter] = GetAtt(self.stack_outputs[parameter], 'Outputs.' + parameter)

            # Finally if nothing else matches copy the child templates parameter to this template's parameter list
            # so the value will pass through this stack down to the child.
            else:
                new_param = self.add_parameter(child_template.parameters[parameter])
                stack_params[parameter] = Ref(new_param)

        return stack_params


    def get_subnet_type(self, subnet_layer):
        """
        Return the subnet type (public/private) that subnet_layer belongs to
        """
        for subnet_type in self._subnets:
            for a_subnet_layer in self._subnets[subnet_type]:
                if a_subnet_layer == subnet_layer:
                    return subnet_type
        return None

