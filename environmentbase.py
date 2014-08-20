'''Base Environment Generator 

This class and command line tool is intended to simplify creating consistent networks from region to region with a good ability to configure a number of pertinent configuration-level options.  

Usage:
    environmentbase.py (-h | --help)
    environmentbase.py --version
    environmentbase.py create --config <CONFIG> [--output <OUTPUT>] [--aws_access_key <AWS_ACCESS_KEY>] [--aws_secret_key <AWS_SECRET_KEY>]
    environmentbase.py create [--output <OUTPUT>] [--default_aws_region <DEFAULT_AWS_REGION>] [--aws_access_key_id <AWS_ACCESS_KEY_ID>] [--strings_patn <STRINGS_PATH>]
                              [--aws_secret_access_key <AWS_SECRET_ACCESS_KEY>] [--ami_map_file <AMI_MAP_FILE>] [--public_subnet_count <PUBLIC_SUBNET_COUNT>] 
                              [--private_subnet_count <PRIVATE_SUBNET_COUNT>] [--public_subnet_size <PUBLIC_SUBNET_SIZE>] [--private_subnet_size <PRIVATE_SUBNET_SIZE>] 
                              [--network_cidr_base <NETWORK_CIDR_BASE>] [--network_cidr_size <NETWORK_CIDR_SIZE>] 
                              [--first_network_address_block <FIRST_NETWORK_ADDRESS_BLOCK>] [--environment_name <ENVIRONMENT_NAME>] [--print_debug]
    environmentbase.py validate (--file <FILE> | --contents <CONTENTS>) [--validation_output_name <VALIDATION_OUTPUT_NAME>]

Options:
    -h --help                                       Show this screen
    --version                                       Show version
    --config <CONFIG>                               path of the configuration file to pull in representing the argument set to pass to the environment build process
    --output <OUTPUT>                               File name to write output of template generation to [Default: environmentbase.template]
    --strings_path <STRINGS_PATH>                   Path to the strings.json file to populate string constants for use within the template [Default: strings.json]
    --default_aws_region <DEFAULT_AWS_REGION>       Default region to use when querying boto for VPC data
    --aws_access_key_id <AWS_ACCESS_KEY_ID>         AWS Access Key Id to use when authenticating to the AWS API
    --aws_secret_access_key <AWS_SECRET_ACCESS_KEY> AWS Secret Access Key to use when authenticating to the AWS API
    --print_debug                                   Optionally prints the CloudFormation output to the console as well as the file specified [Default: False]
    --ami_map_file <AMI_MAP_FILE>                   path of the AMI Map file [Default: ami_cache.json]
    --public_subnet_count <PUBLIC_SUBNET_COUNT>     Number of public subnets to create in this network [Default: 2]
    --private_subnet_count <PRIVATE_SUBNET_COUNT>   Number of private subnets to create in this network [Default: 2]
    --public_subnet_size <PUBLIC_SUBNET_SIZE>       CIDR routing prefix indicating how large the public subnets that are created should be [Default: 24]
    --private_subnet_size <PRIVATE_SUBNET_SIZE>     CIDR routing prefix indicating how large the private subnets that are created should be [Default: 22]
    --network_cidr_base <NETWORK_CIDR_BASE>         Base network address of the network to be created [Default: 172.16.0.0]
    --network_cidr_size <NETWORK_CIDR_SIZE>         CIDR routing prefix indicating how large the entire network to be created should be [Default: 20]
    --first_network_address_block <FIRST_NETWORK_ADDRESS_BLOCK>     Override indicating where within the NETWORK_CIDR_BASE network to start creating subnets
    --validation_output_name <VALIDATION_OUTPUT_NAME>               Name of the CloudFormation Output to place the validation hash [Default: templateValidationHash]
    --cloudtrail_log_prefix <CLOUDTRAIL_LOG_PREFIX> S3 key name prefix to prepend to the bucket created indicating where CloudTrail should ship logs to [Default: cloudtrail_logs]
    --elb_log_prefix <ELB_LOG_PREFIX>               S3 key name prefix to prepend to the bucket created indicating where ELB should ship logs to [Default: elb_logs]
    --environment_name <ENVIRONMENT_NAME>           friendly name to use for describing the environment itself [Default: environmentbase]
    --file <FILE>                                   Path of an existing CloudFormation template to validate
    --contents <CONTENTS>                           String contents of an existing CloudFormation template to validate 
'''
from troposphere import Template, Select, Ref, Parameter, FindInMap, Output, Base64, Join, GetAtt
import troposphere.iam as iam
import troposphere.ec2 as ec2
import troposphere.elasticloadbalancing as elb
import troposphere.autoscaling as autoscaling
import troposphere.s3 as s3
import boto.vpc
import hashlib
import json
from datetime import datetime
from ipcalc import IP, Network
from docopt import docopt

class EnvironmentBase():
    '''
    EnvironmentBase encapsulates functionality required to build and deploy a network and common resources for object storage within a specified region
    '''
    def __init__(self, arg_dict):
        '''
        Init method for environment base creates all common objects for a given environment within the CloudFormation template including a network, s3 bucket and requisite policies to allow ELB Access log aggregation and CloudTrail log storage
        @param arg_dict [dict] keyword arguments to handle setting config-level parameters and arguments within this class
        '''
        self.globals=arg_dict.get('global', {})
        template=arg_dict.get('template', {})
        with open(self.globals.get('strings_path', 'strings.json'), 'r') as f:
            json_data = f.read()
        self.strings = json.loads(json_data)
        self.vpc = None
        self.subnets = {}
        self.template = Template()
        self.template.description = template.get('description', 'No Description Specified')
        self.add_common_parameters(template)
        self.add_ami_mapping(ami_map_file_path=template.get('ami_map_file', 'ami_cache.json'))
        self.add_vpc_az_mapping(boto_config=arg_dict.get('boto', {}))
        self.add_network_cidr_mapping(network_config=arg_dict.get('network', {}))
        self.create_network(network_config=arg_dict.get('network',{}))
        self.add_bastion_instance(bastion_conf=arg_dict.get('bastion', {}))
        
        self.utility_bucket = self.template.add_resource(s3.Bucket('demoUtilityBucket'))
        self.template.add_resource(s3.BucketPolicy('demoUtilityBucketELBLoggingPolicy', 
                Bucket=Ref(self.utility_bucket), 
                PolicyDocument=self.get_elb_logging_bucket_policy_document(self.utility_bucket, elb_log_prefix=self.strings.get('elb_log_prefix',''))))
        self.template.add_resource(s3.BucketPolicy('demoUtilityBucketCloudTrailLoggingPolicy', 
                DependsOn=['demoUtilityBucketELBLoggingPolicy'], 
                Bucket=Ref(self.utility_bucket), 
                PolicyDocument=self.get_cloudtrail_logging_bucket_policy_document(self.utility_bucket, cloudtrail_log_prefix=self.strings.get('cloudtrail_log_prefix', ''))))

    def add_common_parameters(self, template_config):
        '''
        Adds common parameters for instance creation to the CloudFormation template
        @param template_config [dict] collection of template-level configuration values to drive the setup of this method
        @configvalue ec2_key_default [string] name of the EC2 key to use when deploying instances via this template
        '''
        self.template.add_parameter(Parameter('ec2Key', 
                Type='String', 
                Default=template_config.get('ec2_key_default','default-key'), 
                Description='Name of an existing EC2 KeyPair to enable SSH access to the instances',
                AllowedPattern="[\\x20-\\x7E]*",
                MinLength=1, 
                MaxLength=255, 
                ConstraintDescription='can only contain ASCII chacacters.'))

        self.template.add_parameter(Parameter('remoteAccessLocation', 
                Description='CIDR block identifying the network address space that will be allowed to ingress into public access points within this solution',
                Type='String', 
                Default='0.0.0.0/0', 
                MinLength=9, 
                MaxLength=18, 
                AllowedPattern='(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})/(\d{1,2})', 
                ConstraintDescription='must be a valid CIDR range of the form x.x.x.x/x'))

    def add_vpc_az_mapping(self, 
            boto_config):
        '''
        Method gets the AZs within the given account where subnets can be created/deployed
        This is necessary due to some accounts having 4 subnets available within ec2 classic and only 3 within vpc
        which causes the Select by index method of picking azs unpredictable for all accounts
        @param default_aws_region [string] region to use to make the initial connection to the AWS VPC API via boto
        @param aws_access_key [string] AWS Access Key to use when accessing the AWS VPC API
        @param aws_secret_key [string] AWS Secret Key to use when accessing the AWS VPC API
        '''
        az_dict = {}
        region_list = []
        aws_auth_info = {}
        if 'aws_access_key_id' in boto_config and 'aws_secret_access_key' in boto_config:
            aws_auth_info['aws_access_key_id'] = boto_config.get('aws_access_key_id')
            aws_auth_info['aws_secret_access_key'] = boto_config.get('aws_secret_access_key')
        conn = boto.vpc.connect_to_region(region_name=boto_config.get('default_aws_region', 'us-east-1'), **aws_auth_info)
        for region in conn.get_all_regions():
            region_list.append(region.name)
            az_list = boto.vpc.connect_to_region(region.name, **aws_auth_info).get_all_zones()
            if len(az_list) > 1:
                az_dict[region.name] = {}
                for x in range(0, 2):
                    self.add_region_map_value(region.name, 'az' + str(x) + 'Name', az_list[x].name)

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

    def get_cloudtrail_logging_bucket_policy_document(self, 
            utility_bucket, 
            cloudtrail_log_prefix='cloudtrail_logs'):
        '''
        Method builds the S3 bucket policy statements which will allow the proper AWS account ids to write CloudTrail logs to the specified bucket and prefix.
        Per documentation located at: http://docs.aws.amazon.com/awscloudtrail/latest/userguide/aggregating_logs_regions_bucket_policy.html
        @param utility_bucket [Troposphere.s3.Bucket] object reference of the utility bucket for this tier
        @param cloudtrail_log_prefix [string] s3 key name prefix to prepend to the path where CloudTrail will store logs
        '''
        if cloudtrail_log_prefix != None and cloudtrail_log_prefix != '':
            cloudtrail_log_prefix = cloudtrail_log_prefix + '/'
        else:
            cloudtrail_log_prefix = ''

        statements = [{"Action" : ["s3:GetBucketAcl"], 
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

        self.template.add_output(Output('cloudTrailLoggingBucketAndPath', 
                Value=Join('',['arn:aws:s3:::', Ref(utility_bucket), cloudtrail_log_prefix]), 
                Description='S3 bucket and key name prefix to use when configuring CloudTrail to aggregate logs to S3'))

        return {"Statement": statements}

    def get_elb_logging_bucket_policy_document(self, 
            utility_bucket, 
            elb_log_prefix='elb_logs'):
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
                       "Principal" : {"AWS": [FindInMap('RegionMap', Ref('AWS::Region'), 'elbAccountId')]}}]
        
        self.template.add_output(Output('elbAccessLoggingBucketAndPath', 
                Value=Join('',['arn:aws:s3:::', Ref(utility_bucket), elb_log_prefix]), 
                Description='S3 bucket and key name prefix to use when configuring elb access logs to aggregate to S3'))

        return {"Statement":statements}

    def add_ami_mapping(self, 
            ami_map_file_path='ami_cache.json'):
        '''
        Method gets the ami cache from the file locally and adds a mapping for ami ids per region into the template
        This depdns on populating ami_cache.json with the AMI ids that are output by the packer scripts per region
        @param ami_map_file [string] path representing where to find the AMI map to ingest into this template
        '''
        with open(ami_map_file_path, 'r') as json_file:
            json_data = json.load(json_file)
        for region in json_data:
            for key in json_data[region]:
                self.add_region_map_value(region, key, json_data[region][key])

    def create_network(self, 
            network_config=None):
        '''
        Method creates a network with the specified number of public and private subnets within the VPC cidr specified by the networkAddresses CloudFormation mapping
        @param network_config [dict] collection of network parameters for creating the VPC network
        @classarg public_subnet_count [int] number of public subnets to create
        @classarg private_subnet_count [int] number of private subnets to create
        '''
        self.vpc = self.template.add_resource(ec2.VPC('vpc', 
                CidrBlock=FindInMap('networkAddresses', 'vpcBase', 'cidr'), 
                EnableDnsSupport=True, 
                EnableDnsHostnames=True))

        igw = self.template.add_resource(ec2.InternetGateway('vpcIgw'))

        self.template.add_resource(ec2.VPCGatewayAttachment('igwVpcAttachment', 
                InternetGatewayId=Ref(igw), 
                VpcId=Ref(self.vpc)))

        nat_instance_type = self.template.add_parameter(Parameter('natInstanceType', 
                Type='String',
                Default=str(network_config.get('nat_instance_type', 'm1.small')), 
                AllowedValues=self.strings['valid_instance_types'], 
                ConstraintDescription=self.strings['valid_instance_type_message'], 
                Description='Instance type to use when launching NAT instances.'))

        for x in range(0, max(int(network_config.get('public_subnet_count', 2)), int(network_config.get('private_subnet_count', 2)))):
            for y in ['public', 'private']:
                if y in self.template.mappings['networkAddresses']['subnet' + str(x)]:
                    if y not in self.subnets:
                        self.subnets[y] = {}
                    self.subnets[y][str(x)] = self.template.add_resource(ec2.Subnet(y + 'Subnet' + str(x), 
                        AvailabilityZone=FindInMap('RegionMap', Ref('AWS::Region'), 'az' + str(x) + 'Name'), 
                        VpcId=Ref(self.vpc), 
                        CidrBlock=FindInMap('networkAddresses', 'subnet' + str(x), y)))
                    route_table = self.template.add_resource(ec2.RouteTable(y + 'Subnet' + str(x) + 'RouteTable', 
                            VpcId=Ref(self.vpc)))
                    if y == 'public':
                        self.template.add_resource(ec2.Route(y + 'Subnet' + str(x) + 'EgressRoute', 
                                DestinationCidrBlock='0.0.0.0/0', 
                                GatewayId=Ref(igw), 
                                RouteTableId=Ref(route_table)))
                    elif y == 'private':
                        nat_instance = self.create_nat_instance(x, nat_instance_type, 'public')
                        self.template.add_resource(ec2.Route(y + 'Subnet' + str(x) + 'EgressRoute', 
                                DestinationCidrBlock='0.0.0.0/0', 
                                InstanceId=Ref(nat_instance), 
                                RouteTableId=Ref(route_table)))
                    self.template.add_resource(ec2.SubnetRouteTableAssociation(y + 'Subnet' + str(x) + 'EgressRouteTableAssociation', 
                            RouteTableId=Ref(route_table), 
                            SubnetId=Ref(self.subnets[y][str(x)])))

    def create_nat_instance(self, 
            nat_subnet_number,
            nat_instance_type=None,
            nat_subnet_type='public'):
        '''
        Method creates a NAT instance for the private subnet within the specified corresponding subnet
        @param nat_subnet_number [int] ID of the subnet that the NAT instance will be deployed to
        @param nat_instance_type [string | Troposphere.Parameter] instance type to be set when launching the NAT instance
        @param nat_subnet_type [string] type of subnet (public/private) that this instance will be deployed for (which subnet is going to use this to egress traffic)
        '''
        if nat_subnet_type == 'public':
            source_name = 'private'
        else:
            source_name = 'public'

        if nat_instance_type == None:
            nat_instance_type = 'm1.small'
        elif type(nat_instance_type) == Parameter:
            nat_instance_type = Ref(nat_instance_type)

        nat_sg = self.template.add_resource(ec2.SecurityGroup(nat_subnet_type + 'Subnet' + str(nat_subnet_number) + 'SecurityGroup', 
                VpcId=Ref(self.vpc), 
                GroupDescription='Security Group for the ' + nat_subnet_type + ' subnet for az ' + str(nat_subnet_number), 
                SecurityGroupIngress=[
                    ec2.SecurityGroupRule(
                            IpProtocol='-1', 
                            FromPort='-1', 
                            ToPort='-1', 
                            CidrIp=FindInMap('networkAddresses', 'subnet' + str(nat_subnet_number), source_name))],
                SecurityGroupEgress=[
                    ec2.SecurityGroupRule(
                            IpProtocol='-1', 
                            FromPort='-1', 
                            ToPort='-1', 
                            CidrIp='0.0.0.0/0')]))

        return self.template.add_resource(ec2.Instance(nat_subnet_type + str(nat_subnet_number) + 'NATInstance', 
                AvailabilityZone=FindInMap('RegionMap', Ref('AWS::Region'), 'az' + str(nat_subnet_number) + 'Name'), 
                ImageId=FindInMap('RegionMap', Ref('AWS::Region'), 'natAmiId'), 
                KeyName=Ref(self.template.parameters['ec2Key']), 
                InstanceType=nat_instance_type,
                NetworkInterfaces=[ec2.NetworkInterfaceProperty(
                        AssociatePublicIpAddress=True, 
                        DeleteOnTermination=True, 
                        DeviceIndex='0', 
                        GroupSet=[Ref(nat_sg)], 
                        SubnetId=Ref(self.subnets[nat_subnet_type][str(nat_subnet_number)]))],
                SourceDestCheck=False))

    def add_network_cidr_mapping(self, 
        network_config):
        '''
        Method calculates and adds a CloudFormation mapping that is used to set VPC and Subnet CIDR blocks.  Calculated based on CIDR block sizes and additionally checks to ensure all network segments fit inside of the specified overall VPC CIDR
        @param network_config [dict] dictionary of values containing data for creating 
        @configvalue network_cidr_base [string] base IP address to use for the overall VPC network deployment
        @configvalue network_cidr_size [string] routing prefix size of the overall VPC network to be deployed
        @configvalue first_network_address_block [string | None] optional parameter identifying the first network to use when calculating subnet addresses.  Useful for setting subnet sizes that don't start at the first address within the VPC
        @configvalue public_subnet_size [string] routing prefix size to be used when creating public subnets
        @configvalue private_subnet_size [string] routing prefix size to be used when creating private subnets
        @configvalue public_subnet_count [int] number of public subnets to create in sequential order starting from the first_network_address_block address if present or the vpc_cidr_base address as a default_instance_type
        @configvalue private_subnet_count [int] number of private subnets to create in sequential order starting from the last public subnet created
        '''
        public_subnet_count = int(network_config.get('public_subnet_count', 2))
        private_subnet_count = int(network_config.get('private_subnet_count', 2))
        public_subnet_size = str(network_config.get('public_subnet_size', '24'))
        private_subnet_size = str(network_config.get('private_subnet_size', '22'))
        network_cidr_base = str(network_config.get('network_cidr_base', '172.16.0.0'))
        network_cidr_size = str(network_config.get('network_cidr_size', '20'))
        first_network_address_block = str(network_config.get('first_network_address_block', network_cidr_base))

        ret_val = {}
        cidr_info = Network(network_cidr_base + '/' + network_cidr_size)
        ret_val['vpcBase'] = {'cidr': cidr_info.network().to_tuple()[0] + '/' + str(cidr_info.to_tuple()[1])}
        current_base_address = first_network_address_block
        for public_subnet_id in range(0, public_subnet_count):
            if not cidr_info.check_collision(current_base_address):
                raise RuntimeError('Cannot continue creating network--current base address is outside the range of the master Cidr block. Found on pass ' + str(public_subnet_id + 1) + ' when creating public subnet cidrs')
            ip_info = Network(current_base_address + '/' + str(public_subnet_size))
            range_info = ip_info.network().to_tuple()
            if 'subnet' + str(public_subnet_id) not in ret_val:
                ret_val['subnet' + str(public_subnet_id)] = dict()
            ret_val['subnet' + str(public_subnet_id)]['public'] = ip_info.network().to_tuple()[0] + '/' + str(ip_info.to_tuple()[1])
            current_base_address = IP(int(ip_info.host_last().hex(), 16) + 2).to_tuple()[0]
        range_reset = Network(current_base_address + '/' + str(private_subnet_size))
        current_base_address = IP(int(range_reset.host_last().hex(), 16) + 2).to_tuple()[0]
        for private_subnet_id in range(0, private_subnet_count):
            if not cidr_info.check_collision(current_base_address):
                raise RuntimeError('Cannot continue creating network--current base address is outside the range of the master Cidr block. Found on pass ' + str(private_subnet_id + 1) + ' when creating private subnet cidrs')
            ip_info = Network(current_base_address + '/' + str(private_subnet_size))
            range_info = ip_info.network().to_tuple()
            if 'subnet' + str(private_subnet_id) not in ret_val:
                ret_val['subnet' + str(private_subnet_id)] = dict()
            ret_val['subnet' + str(private_subnet_id)]['private'] = ip_info.network().to_tuple()[0] + '/' + str(ip_info.to_tuple()[1])
            current_base_address = IP(int(ip_info.host_last().hex(), 16) + 2).to_tuple()[0]
        return self.template.add_mapping('networkAddresses', ret_val)

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
            root_volume_size=24,
            include_ephemerals=True, 
            number_ephemeral_vols=2,
            ebs_data_volumes=None, #[{'size':'100', 'type':'gp2', 'delete_on_termination': True, 'iops': 4000, 'volume_type': 'io1'}]
            custom_tags=None, 
            load_balancer=None,
            subnet_type='private'):
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
        @param instance_monitoring [Boolean] indicates that detailed monitoring should be turned on for all instnaces launched within this Auto Scaling group
        @param custom_tags [Troposphere.autoscaling.Tag[]] Collection of Auto Scaling tags to be assigned to the Auto Scaling Group
        @param load_balancer [Troposphere.elasticloadbalancing.LoadBalancer] Object reference to an ELB to be assigned to this auto scaling group
        @param subnet_type [string {'public', 'private'}] string indicating which type of subnet (public or private) instances should be launched into
        '''
        if subnet_type not in ['public', 'private']:
            raise RuntimeError('Unable to determine which type of subnet instances should be launched into. ' + str(subnet_type) + ' is not one of ["public", "private"].')

        if ec2_key != None and type(ec2_key) != Parameter:
            ec2_key = Ref(ec2_key)
        else:
            ec2_key = Ref(self.template.parameters['ec2Key'])

        if default_instance_type == None:
            default_instance_type = 'm1.small'

        if instance_type == None or type(instance_type) == str:
            instance_type = self.template.add_parameter(Parameter(layer_name + 'InstanceType', 
                    Type='String', 
                    Default=default_instance_type, 
                    Description='Instance type for instances launched within the ' + layer_name + ' auto scaling group', 
                    AllowedValues=self.strings['valid_instance_types'], 
                    ConstraintDescription=self.strings['valid_instance_type_message']))

        sg_list = []
        for sg in security_groups:
            sg_list.append(Ref(sg))

        launch_config_obj = autoscaling.LaunchConfiguration(layer_name + 'LaunchConfiguration', 
                IamInstanceProfile=Ref(instance_profile), 
                ImageId=FindInMap('RegionMap', Ref('AWS::Region'), ami_name), 
                InstanceType=Ref(instance_type), 
                SecurityGroups=sg_list, 
                KeyName=ec2_key, 
                UserData=user_data, 
                InstanceMonitoring=instance_monitoring)

        block_devices = [ec2.BlockDeviceMapping(
                DeviceName='/dev/sda1', 
                Ebs=ec2.EBSBlockDevice(
                VolumeSize=root_volume_size))]
        
        device_names = ['/dev/sdb', '/dev/sdc', '/dev/sdd', '/dev/sde', '/dev/sdf', '/dev/sdg', '/dev/sdh', '/dev/sdi', '/dev/sdj', '/dev/sdk', '/dev/sdl', '/dev/sdm', '/dev/sdn', '/dev/sdo', '/dev/sdp']

        if ebs_data_volumes != None and len(ebs_data_volumes) > 0: 
            for ebs_volume in ebs_data_volumes:
                device_name = device_names.pop()
                ebs_block_device = ec2.EbsBlockDevice(
                                DeleteOnTermination=ebs_volume.get('delete_on_termination', True), 
                                VolumeSize=ebs_volume.get('size', '100'), 
                                VolumeType=ebs_volume.get('type', 'gp2'))
                
                if 'iops' in ebs_volume: 
                    ebs_block_device.Iops = ebs_volume.get('iops')
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
                AvailabilityZones=[FindInMap('RegionMap', Ref('AWS::Region'), 'az0Name'), FindInMap('RegionMap', Ref('AWS::Region'), 'az1Name')],
                LaunchConfigurationName=Ref(launch_config), 
                MaxSize=max_size, 
                MinSize=min_size, 
                DesiredCapacity=min(min_size, max_size), 
                VPCZoneIdentifier=[Ref(self.subnets[subnet_type][str(0)]), Ref(self.subnets[subnet_type][str(1)])])

        if load_balancer != None:
            auto_scaling_obj.LoadBalancerNames = [Ref(load_balancer)]

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
        @param region_list [string[]]] array of strings representing the names of the regions to validate and/or create within the RegionMap CloudFormation mapping
        '''
        if 'RegionMap' not in self.template.mappings:
            self.template.mappings['RegionMap'] = {}
        for region_name in region_list:
            if region_name not in self.template.mappings['RegionMap']:
                self.template.mappings['RegionMap'][region_name] = {}

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

    def add_bastion_instance(self, 
            bastion_conf):
        '''
        Method adds a bastion host to the environment and outputs the address of the bastion when deployed
        @param bastion_conf [dict] dictionary of configuration values used for populating the bastion host creation process
        @configvalue bastion_instance_type_default [string] default to for the instance type parameter for the bastion host
        @configvalue remote_access_cidr [string | cidr format] default for the parameter gathering the cidr to allow remote access from for the bastion host
        '''
        instance_type = self.template.add_parameter(Parameter('bastionInstanceType', 
                Default=bastion_conf.get('instance_type_default', 't1.micro'), 
                AllowedValues=self.strings['valid_instance_types'], 
                Type='String',
                Description='Instance type to use when launching the Bastion host for access to resources that are not publicly exposed', 
                ConstraintDescription=self.strings['valid_instance_type_message']))

        bastion_security_group = self.template.add_resource(ec2.SecurityGroup('bastionSecurityGroup', 
                VpcId=Ref(self.vpc), 
                GroupDescription='Security group allowing ingress via SSH to this instance along with other standard accessbility port rules', 
                SecurityGroupIngress=[ec2.SecurityGroupRule(
                        FromPort='22', 
                        ToPort='22', 
                        IpProtocol='tcp', 
                        CidrIp=Ref(self.template.parameters['remoteAccessLocation']))],
                SecurityGroupEgress=[ec2.SecurityGroupRule(
                        FromPort='22', 
                        ToPort='22', 
                        IpProtocol='tcp', 
                        CidrIp=FindInMap('networkAddresses', 'vpcBase', 'cidr')), 
                    ec2.SecurityGroupRule(
                        FromPort='80', 
                        ToPort='80', 
                        IpProtocol='tcp', 
                        CidrIp='0.0.0.0/0'), 
                    ec2.SecurityGroupRule(
                        FromPort='443', 
                        ToPort='443', 
                        IpProtocol='tcp',
                        CidrIp='0.0.0.0/0')]))

        bastion_instance = self.template.add_resource(ec2.Instance('bastionInstance', 
               ImageId=FindInMap('RegionMap', Ref('AWS::Region'), 'ubuntu1404LtsAmiId'),
               InstanceType=Ref(instance_type),
               KeyName=Ref(self.template.parameters['ec2Key']),
               NetworkInterfaces=[ec2.NetworkInterfaceProperty(
                    AssociatePublicIpAddress=True, 
                    DeleteOnTermination=True, 
                    Description='ENI for the bastion host', 
                    DeviceIndex='0', 
                    GroupSet=[Ref(bastion_security_group)], 
                    SubnetId=Ref(self.subnets['public'][str('0')]))],
               Tags=[ec2.Tag('Name', 'bastionHost')],
               Monitoring=True))

        self.template.add_output(Output('bastionHostAddress', 
            Value=GetAtt(bastion_instance, 'PublicDnsName'), 
            Description='Address to use when accessing the bastion host.'))

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
        if from_port == to_port:
            label_suffix = ip_protocol.capitalize() + from_port
        else:
            label_suffix = ip_protocol.capitalize() + from_port + 'To' + to_port

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

    @staticmethod
    def build_bootstrap(bootstrap_files, 
            variable_declarations=None, 
            cleanup_commands=None):
        '''
        Method encapsulates process of building out the bootstrap given a set of variables and a bootstrap file to source from
        Returns base 64-wrapped, joined bootstrap to be applied to an instnace
        @param bootstrap_files [ string[] ] list of paths to the bash script(s) to read as the source for the bootstrap action to created
        @param variable_declaration [ list ] list of lines to add to the head of the file - used to inject bash variables into the script
        @param cleanup_commnds [ string[] ] list of lines to add at the end of the file - used for layer-specific details
        '''
        ret_val = ['#!/bin/bash']
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
    def create_instance_profile(self, 
            layer_name, 
            iam_policies=None):
        '''
        Helper method creates an IAM Role and Instance Profile for the optoinally specified IAM policies
        @param layer_name [string] friendly name for the Role and Instance Profile used for naming and path organization
        @param iam_policies [Troposphere.iam.Policy[]] array of IAM Policies to be associated with the Role and Instance Profile created
        @classarg environment_name [string] friendly name for the environment at large
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

if __name__ == '__main__':
    args = docopt(__doc__, version='EnvironmentBase 1.0')
    if '--print_debug' in args and args['--print_debug']:
        print args
    if args['validate']:
        if args['--file']:
            EnvironmentBase.validate_template_file(args['--file'], args['--validation_output_name'])
        elif args['--contents']:
            EnvironmentBase.validate_template_contents(args['--contents'], args['--validation_output_name'])            
        else:
            raise RuntimeError('Cannot validate a template as neither the --file or the --contents arguments are set.')
    elif args['create']:
        cmd_args = {}
        if args['--config']:
            with open(args['--config'], 'r') as f:
                cmd_args = json.loads(f.read())
        else:
            for arg in args.keys():
                if args[arg] and arg.startswith('--'):
                    cmd_args[arg.replace('--', '')] = args[arg]
        env_base = EnvironmentBase(cmd_args)
        if 'print_debug' in cmd_args and cmd_args['print_debug']:
            print env_base.to_json()
        if 'output' in cmd_args:
            with open(cmd_args['output'], 'w') as output_file:
                output_file.write(env_base.to_json())