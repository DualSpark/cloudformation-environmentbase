import os
import os.path
from troposphere import Template, Select, Ref, Parameter, FindInMap, Output, Base64, Join, GetAtt
import troposphere.iam as iam
import troposphere.ec2 as ec2
import troposphere.autoscaling as autoscaling
import troposphere.cloudformation as cf
import troposphere.route53 as r53
import hashlib
import json
import boto
import time
import boto.s3
from boto.s3.key import Key
from datetime import datetime

class EnvironmentBase():
    '''
    EnvironmentBase encapsulates functionality required to build and deploy a network and common resources for object storage within a specified region
    '''
    def __init__(self, arg_dict):
        '''
        Init method for environment base creates all common objects for a given environment within the CloudFormation template including a network, s3 bucket and requisite policies to allow ELB Access log aggregation and CloudTrail log storage
        @param arg_dict [dict] keyword arguments to handle setting config-level parameters and arguments within this class
        '''
        self.globals                    = arg_dict.get('global', {})
        self.template_args              = arg_dict.get('template', {})

        self.template                   = Template()
        self.template.description       = self.template_args.get('description', 'No Description Specified')

        self.manual_parameter_bindings  = {}
        self.subnets                    = {}
        self.ignore_outputs             = ['templateValidationHash', 'dateGenerated']
        self.strings                    = self.__build_common_strings()

        self.add_common_parameters(self.template_args)

        local_amicache = os.path.join(os.getcwd(), 'ami_cache.json')
        if os.path.isfile(local_amicache):
            file_path = local_amicache
        elif os.path.isfile('ami_cache.json'):
            file_path = 'ami_cache.json'
        else:
            file_path = os.path.join(os.path.dirname(__file__), 'ami_cache.json')

        ami_map_file = self.template_args.get('ami_map_file', file_path)
        self.add_ami_mapping(ami_map_file_path=ami_map_file)

    def register_elb_to_dns(self, elb, tier_name, tier_args):
        '''
        Method handles the process of uniformly creating CNAME records for ELBs in a given tier
        @param elb [Troposphere.elasticloadbalancing.LoadBalancer]
        @param tier_name [str]
        @param tier_args [dict]
        '''
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

    @staticmethod
    def __build_common_strings():
        return { #"valid_instance_types": ["t1.micro","m3.medium","m3.large","m3.xlarge","m3.2xlarge","m1.small","m1.medium","m1.large","m1.xlarge","c3.large","c3.xlarge","c3.2xlarge","c3.4xlarge","c3.8xlarge","c1.medium","c1.xlarge","cc2.xlarge","g2.2xlarge","cg1.4xlarge","m2.xlarge","m2.2xlarge","m2.4xlarge","cr1.8xlarge","i2.xlarge","i2.2xlarge","i2.4xlarge","hs1.8xlarge","hs1.4xlarge"],
                "valid_instance_types": ["t2.micro", "t2.small", "t2.medium",
                                         "m3.medium", "m3.large", "m3.xlarge", "m3.2xlarge",
                                         "c4.large", "c4.xlarge", "c4.2xlarge", "c4.4xlarge", "c4.8xlarge",
                                         "c3.large", "c3.xlarge", "c3.2xlarge", "c3.4xlarge", "c3.8xlarge",
                                         "r3.large", "r3.xlarge", "r3.2xlarge", "r3.4xlarge", "r3.8xlarge",
                                         "i2.xlarge", "i2.2xlarge", "i2.4xlarge", "i2.8xlarge",
                                         "d2.xlarge", "d2.2xlarge", "d2.4xlarge", "d2.8xlarge",
                                         "g2.2xlarge"],
                "valid_instance_type_message": "must be a valid EC2 instance type.",
                # "valid_db_instance_types" : ["db.t1.micro","db.m1.small","db.m1.medium","db.m1.large","db.m1.xlarge","db.m2.xlarge","db.m2.2xlarge","db.m2.4xlarge","db.cr1.8xlarge"],
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
                "url_regex": "^(https?|ftp|file)://[-a-zA-Z0-9+&@#/%?=~_|!:,.;]*[-a-zA-Z0-9+&@#/%=~_|]"}

    def add_common_parameters(self, template_config):
        '''
        Adds common parameters for instance creation to the CloudFormation template
        @param template_config [dict] collection of template-level configuration values to drive the setup of this method
        '''
        if 'ec2Key' not in self.template.parameters:
            self.template.add_parameter(Parameter('ec2Key',
                    Type='String',
                    Default=template_config.get('ec2_key_default','default-key'),
                    Description='Name of an existing EC2 KeyPair to enable SSH access to the instances',
                    AllowedPattern="[\\x20-\\x7E]*",
                    MinLength=1,
                    MaxLength=255,
                    ConstraintDescription='can only contain ASCII chacacters.'))

        if 'remoteAccessLocation' not in self.template.parameters:
            self.remote_access_cidr = self.template.add_parameter(Parameter('remoteAccessLocation',
                    Description='CIDR block identifying the network address space that will be allowed to ingress into public access points within this solution',
                    Type='String',
                    Default='0.0.0.0/0',
                    MinLength=9,
                    MaxLength=18,
                    AllowedPattern='(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})/(\d{1,2})',
                    ConstraintDescription='must be a valid CIDR range of the form x.x.x.x/x'))

    def add_region_map_value(self,
            region,
            key,
            value):
        '''
        Method adds a key value pair to the RegionMap mapping within this CloudFormation template
        @param region [string] AWS region name that the key value pair is associated with
        @param key [string] name of the key to store in the RegionMap mapping for the specified Region
        @param value [string] value portion of the key value pair related to the region specified
        '''
        self.__init_region_map([region])
        if region not in self.template.mappings['RegionMap']:
            self.template.mappings['RegionMap'][region] = {}
        self.template.mappings['RegionMap'][region][key] = value

    def get_logging_bucket_policy_document(self,
            utility_bucket,
            elb_log_prefix='elb_logs',
            cloudtrail_log_prefix='cloudtrail_logs'):
        '''
        Method builds the S3 bucket policy statements which will allow the proper AWS account ids to write ELB Access Logs to the specified bucket and prefix.
        Per documentation located at: http://docs.aws.amazon.com/ElasticLoadBalancing/latest/DeveloperGuide/configure-s3-bucket.html
        @param utility_bucket [Troposphere.s3.Bucket] object reference of the utility bucket for this tier
        @param elb_log_prefix [string] prefix for paths used to prefix the path where ELB will place access logs
        '''
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

    def add_ami_mapping(self, ami_map_file_path='ami_cache.json'):
        '''
        Method gets the ami cache from the file locally and adds a mapping for ami ids per region into the template
        This depends on populating ami_cache.json with the AMI ids that are output by the packer scripts per region
        @param ami_map_file [string] path representing where to find the AMI map to ingest into this template
        '''
        with open(ami_map_file_path, 'r') as json_file:
            json_data = json.load(json_file)
        for region in json_data:
            for key in json_data[region]:
                self.add_region_map_value(region, key, json_data[region][key])

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
            depends_on=None):
        '''
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
        '''
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

        if depends_on:
            auto_scaling_obj = autoscaling.AutoScalingGroup(layer_name + 'AutoScalingGroup',
                AvailabilityZones=self.azs,
                LaunchConfigurationName=Ref(launch_config),
                MaxSize=max_size,
                MinSize=min_size,
                DesiredCapacity=min(min_size, max_size),
                VPCZoneIdentifier=self.subnets[subnet_type.lower()],
                TerminationPolicies=['OldestLaunchConfiguration', 'ClosestToNextInstanceHour', 'Default'],
                DependsOn=depends_on)
        else:
            auto_scaling_obj = autoscaling.AutoScalingGroup(layer_name + 'AutoScalingGroup',
                AvailabilityZones=self.azs,
                LaunchConfigurationName=Ref(launch_config),
                MaxSize=max_size,
                MinSize=min_size,
                DesiredCapacity=min(min_size, max_size),
                VPCZoneIdentifier=self.subnets[subnet_type.lower()],
                TerminationPolicies=['OldestLaunchConfiguration', 'ClosestToNextInstanceHour', 'Default'])

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
        '''
        Internal helper method used to check to ensure mapping dictionaries are present
        @param region_list [list(str)] array of strings representing the names of the regions to validate and/or create within the RegionMap CloudFormation mapping
        '''
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
        '''
        Helper method creates reciprocal ingress and egress rules given two existing security groups and a set of ports
        @param source_group [Troposphere.ec2.SecurityGroup] Object reference to the source security group
        @param source_group_name [string] friendly name of the source security group used for labels
        @param destination_group [Troposphere.ec2.SecurityGroup] Object reference to the destination security group
        @param destination_group_name [string] friendly name of the destination security group used for labels
        @param from_port [string] lower boundary of the port range to set for the secuirty group rules
        @param to_port [string] upper boundary of the port range to set for the security group rules
        @param ip_protocol [string] name of the IP protocol to set this rule for
        '''
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

        self.template.add_resource(ec2.SecurityGroupIngress(destination_group_name + 'Ingress' + source_group_name + label_suffix,
            SourceSecurityGroupId=Ref(source_group),
            GroupId=Ref(destination_group),
            FromPort=from_port,
            ToPort=to_port,
            IpProtocol=ip_protocol))

        self.template.add_resource(ec2.SecurityGroupEgress(source_group_name + 'Egress' + destination_group_name + label_suffix,
            DestinationSecurityGroupId=Ref(destination_group),
            GroupId=Ref(source_group),
            FromPort=from_port,
            ToPort=to_port,
            IpProtocol=ip_protocol))

    def to_json(self):
        '''
        Centralized method for managing outputting this template with a timestamp identifying when it was generated and for creating a SHA256 hash representing the template for validation purposes
        '''
        if 'dateGenerated' not in self.template.outputs:
            self.template.add_output(Output('dateGenerated',
                Value=str(datetime.utcnow()),
                Description='UTC datetime representation of when this template was generated'))
        if 'templateValidationHash' not in self.template.outputs:
            m = hashlib.sha256()
            m.update(EnvironmentBase.__validation_formatter(self.template))
            self.template.add_output(Output('templateValidationHash',
                Value=m.hexdigest(),
                Description='Hash of this template that can be used as a simple means of validating whether a template has been changed since it was generated.'))
        return self.template.to_json()

    @staticmethod
    def validate_template_file(cloudformation_template_path,
            validation_output_name='templateValidationHash'):
        '''
        Method takes a file path, reads it and validates the template via the SHA256 checksum that is to be located within the Outputs collection of the cloudFormation template
        @param cloudformation_template_path [string] path from which to read the cloudformation template
        @param validation_output_name [string] name of the output to use to gather the SHA256 hash to validate
        '''
        with open(cloudformation_template_path, 'r') as f:
            cf_template_contents = f.read()
        return EnvironmentBase.validate_template_contents(cf_template_contents, validation_output_name)

    @staticmethod
    def build_bootstrap(bootstrap_files,
            variable_declarations=None,
            cleanup_commands=None,
            prepend_line='#!/bin/bash'):
        '''
        Method encapsulates process of building out the bootstrap given a set of variables and a bootstrap file to source from
        Returns base 64-wrapped, joined bootstrap to be applied to an instnace
        @param bootstrap_files [ string[] ] list of paths to the bash script(s) to read as the source for the bootstrap action to created
        @param variable_declaration [ list ] list of lines to add to the head of the file - used to inject bash variables into the script
        @param cleanup_commnds [ string[] ] list of lines to add at the end of the file - used for layer-specific details
        '''
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
        '''
        Method encpsulates reading a file into a list while removing newline characters
        @param file_name [string] path to file to read
        '''
        ret_val = []
        with open(file_name) as f:
            content = f.readlines()
        for line in content:
            if not line.startswith('#~'):
                ret_val.append(line.replace("\n", ""))
        return ret_val

    @staticmethod
    def __validation_formatter(cf_template):
        '''
        Validation formatter helps to ensure consistent formatting for hash validation workflow
        @param json_string [string | Troposphere.Template | dict] JSON-able data to be formatted for validation
        '''
        if type(cf_template) == Template:
            json_string = json.dumps(json.loads(cf_template.to_json()))
        elif type(cf_template) == dict:
            json_string = json.dumps(cf_template)
        return json.dumps(json.loads(json_string), separators=(',',':'))

    @staticmethod
    def validate_template_contents(cloudformation_template_string,
            validation_output_name='templateValidationHash'):
        '''
        Method takes the contents of a CloudFormation template and validates the SHA256 hash
        @param cloudformation_template_string [string] string contents of the CloudFormation template to validate
        @param validation_output_name [string] name of the CloudFormation output containing the SHA256 hash to be validated
        '''
        template_object = json.loads(cloudformation_template_string)
        if 'Outputs' in template_object:
            if validation_output_name in template_object['Outputs']:
                if 'Value' in template_object['Outputs'][validation_output_name]:
                    hash_to_validate = template_object['Outputs'][validation_output_name]['Value']
                    del template_object['Outputs'][validation_output_name]
                    m = hashlib.sha256()
                    m.update(EnvironmentBase.__validation_formatter(template_object))
                    template_hash = m.hexdigest()
                    print '* hash to validate: ' + hash_to_validate
                    print '*  calculated hash: ' + template_hash
                    if hash_to_validate == template_hash:
                        print 'Template is valid'
                    else:
                        raise RuntimeError('Template hash is not valid')
                else:
                    print 'Cannot validate this template as it appears it is corrupt.  The [' + validation_output_name + '] output does not contain a value property.'
            else:
                print 'Cannot validate this template as it does not contain the specified output [' + validation_output_name + '] - check to make sure this is the right name and try again.'
        else:
            print 'This template does not contain a collection of outputs. Please check the input template and try again.'


    def create_instance_profile(self,
            layer_name,
            iam_policies=None):
        '''
        Helper method creates an IAM Role and Instance Profile for the optoinally specified IAM policies
        @param layer_name [string] friendly name for the Role and Instance Profile used for naming and path organization
        @param iam_policies [Troposphere.iam.Policy[]] array of IAM Policies to be associated with the Role and Instance Profile created
        '''
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
                depends_on=None):
        '''
        Method adds a child template to this object's template and binds the child template parameters to properties, resources and other stack outputs
        @param name [str] name of this template for key naming in s3
        @param template [Troposphere.Template] Troposphere Template object to add as a child to this object's template
        @param template_args [dict] key-value pair of configuration values for templates to apply to this operation
        @param s3_bucket [str] name of the bucket to upload keys to - will default to value in template_args if not present
        @param s3_key_prefix [str] s3 key name prefix to prepend to s3 key path - will default to value in template_args if not present
        @param s3_canned_acl [str] name of the s3 canned acl to apply to templates uploaded to S3 - will default to value in template_args if not present
        '''
        key_serial = str(int(time.time()))
        if s3_bucket == None:
            s3_bucket = self.template_args.get('s3_bucket')
        if s3_bucket == None:
            raise RuntimeError('Cannot upload template to s3 as a s3 bucket was not specified nor set as a default')
        if s3_key_prefix == None:
            s3_key_prefix = self.template_args.get('s3_key_name_prefix', '')
        if s3_key_prefix == None:
            s3_key_name = '/' +  name + '.' + key_serial + '.template'
        else:
            s3_key_name = s3_key_prefix + '/' + name + '.' + key_serial + '.template'

        s3_canned_acl = self.template_args.get('s3_canned_acl', 'public-read')

        if self.template_args.get('mock_upload',False):
            stack_url = 'http://www.dualspark.com'
        else:
            conn = boto.connect_s3()
            bucket = conn.get_bucket(s3_bucket)
            key = Key(bucket)

            key.key = s3_key_name
            key.set_contents_from_string(template_wrapper.to_json())
            key.set_acl(s3_canned_acl)

            stack_url = key.generate_url(expires_in=0, query_auth=False)
            stack_url = stack_url.split('?')[0]

        if name not in self.stack_outputs:
            self.stack_outputs[name] = []

        template = template_wrapper.template
        stack_params = {}
        for parameter in template.parameters.keys():
            if parameter in self.manual_parameter_bindings:
                stack_params[parameter] = self.manual_parameter_bindings[parameter]
            elif parameter.startswith('availabilityZone'):
                stack_params[parameter] = GetAtt('privateSubnet' + parameter.replace('availabilityZone',''), 'AvailabilityZone')
            elif parameter in self.template.parameters.keys():
                stack_params[parameter] = Ref(self.template.parameters.get(parameter))
            elif parameter in self.template.resources.keys():
                stack_params[parameter] = Ref(self.template.resources.get(parameter))
            elif parameter in self.stack_outputs:
                stack_params[parameter] = GetAtt(self.stack_outputs[parameter], 'Outputs.' + parameter)
            else:
                stack_params[parameter] = Ref(self.template.add_parameter(template.parameters[parameter]))
        stack_name = name + 'Stack'

        # DependsOn needs to go in the constructor of the object
        if depends_on:
            stack_obj = cf.Stack(stack_name,
                TemplateURL=stack_url,
                Parameters=stack_params,
                TimeoutInMinutes=self.template_args.get('timeout_in_minutes', '60'),
                DependsOn=depends_on)

        else:
            stack_obj = cf.Stack(stack_name,
                TemplateURL=stack_url,
                Parameters=stack_params,
                TimeoutInMinutes=self.template_args.get('timeout_in_minutes', '60'))

        return self.template.add_resource(stack_obj)

def main():
    import json
    config_file = os.path.join(os.path.dirname(__file__), 'config_args.json')
    with open(config_file, 'r') as f:
        cmd_args = json.loads(f.read())
    test = EnvironmentBase(cmd_args)
    print test.to_json()

if __name__ == '__main__':
    main()
