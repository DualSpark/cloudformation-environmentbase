from troposphere import Template, Select, Ref, Parameter, FindInMap, Output, Base64, Join, GetAtt
import troposphere.iam as iam
import troposphere.ec2 as ec2
import troposphere.s3 as s3
import troposphere.elasticloadbalancing as elb
import boto.vpc
import boto
import hashlib
import json
from environmentbase import EnvironmentBase
from datetime import datetime
from ipcalc import IP, Network

class NetworkBase(EnvironmentBase):
    '''
    Class creates all of the base networking infrastructure for a common deployment within AWS
    This is intended to be the 'base' template for deploying child templates 
    '''

    def __init__(self, arg_dict):
        '''
        Init method wires up all the required networking resources to deploy this set of infrastructure 
        @param arg_dict [dict] collection of keyword arguments for this class implementation
        '''
        network_config = arg_dict.get('network', {})

        EnvironmentBase.__init__(self, arg_dict)
        self.vpc = None
        self.azs = []

        self.local_subnets = {}
        self.stack_outputs = {}
        self.add_vpc_az_mapping(boto_config=arg_dict.get('boto', {}),
                az_count=max(network_config.get('public_subnet_count', 2), network_config.get('private_subnet_count',2)))
        self.add_network_cidr_mapping(network_config=arg_dict.get('network', {}))
        self.create_network(network_config=network_config)
        self.utility_bucket = self.add_utility_bucket()

        self.common_sg = self.template.add_resource(ec2.SecurityGroup('commonSecurityGroup', 
            GroupDescription='Security Group allows ingress and egress for common usage patterns throughout this deployed infrastructure.',
            VpcId=Ref(self.vpc), 
            SecurityGroupEgress=[ec2.SecurityGroupRule(
                        FromPort='80', 
                        ToPort='80', 
                        IpProtocol='tcp', 
                        CidrIp='0.0.0.0/0'), 
                    ec2.SecurityGroupRule(
                        FromPort='443', 
                        ToPort='443', 
                        IpProtocol='tcp', 
                        CidrIp='0.0.0.0/0'), 
                    ec2.SecurityGroupRule(
                        FromPort='123', 
                        ToPort='123', 
                        IpProtocol='udp', 
                        CidrIp='0.0.0.0/0')], 
            SecurityGroupIngress= [
                    ec2.SecurityGroupRule(
                        FromPort='22', 
                        ToPort='22', 
                        IpProtocol='tcp', 
                        CidrIp=FindInMap('networkAddresses', 'vpcBase', 'cidr'))]))

        for x in range(0, max(int(network_config.get('public_subnet_count', 2)), int(network_config.get('private_subnet_count', 2)))):
            self.azs.append(FindInMap('RegionMap', Ref('AWS::Region'), 'az' + str(x) + 'Name'))


    def add_utility_bucket(self, name='demo'): 
        '''
        Method adds a bucket to be used for infrastructure utility purposes such as backups
        @param name [str] friendly name to prepend to the CloudFormation asset name 
        '''
        self.utility_bucket = self.template.add_resource(s3.Bucket(name.lower() + 'UtilityBucket'))

        bucket_policy_statements = self.get_logging_bucket_policy_document(self.utility_bucket, elb_log_prefix=self.strings.get('elb_log_prefix',''), cloudtrail_log_prefix=self.strings.get('cloudtrail_log_prefix', ''))
        
        self.template.add_resource(s3.BucketPolicy( name.lower() + 'UtilityBucketLoggingPolicy', 
                Bucket=Ref(self.utility_bucket), 
                PolicyDocument=bucket_policy_statements))
        
        self.manual_parameter_bindings['utilityBucket'] = Ref(self.utility_bucket)


    def add_vpc_az_mapping(self, 
            boto_config, 
            az_count=2):
        '''
        Method gets the AZs within the given account where subnets can be created/deployed
        This is necessary due to some accounts having 4 subnets available within ec2 classic and only 3 within vpc
        which causes the Select by index method of picking azs unpredictable for all accounts
        @param boto_config [dict] collection of boto configuration values as set by the configuration file 
        @param az_count [int] number of AWS availability zones to include in the VPC mapping
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
                temp_dict = {}
                x = 0
                for availability_zone in az_list:
                    temp_dict['az' + str(x) + 'Name'] = availability_zone.name
                    x += 1
                if len(temp_dict) >= az_count:
                    az_dict[region.name] = {}
                    for item in temp_dict:
                        self.add_region_map_value(region.name, item, temp_dict[item])

    def add_vpc_gateway(self, igw, vpc):
        pass

    def create_network(self, 
            network_config=None):
        '''
        Method creates a network with the specified number of public and private subnets within the VPC cidr specified by the networkAddresses CloudFormation mapping
        @param network_config [dict] collection of network parameters for creating the VPC network
        '''
        if 'network_name' in network_config: 
            network_name = network_config.get('network_name')
        else:
            network_name = self.__class__.__name__

        self.vpc = self.template.add_resource(ec2.VPC('vpc', 
                CidrBlock=FindInMap('networkAddresses', 'vpcBase', 'cidr'), 
                EnableDnsSupport=True, 
                EnableDnsHostnames=True,
                Tags=[ec2.Tag(key='Name', value=network_name)]))

        self.igw = self.template.add_resource(ec2.InternetGateway('vpcIgw'))

        igw_title = 'igwVpcAttachment'
        self.igw_attachment = self.template.add_resource(ec2.VPCGatewayAttachment(igw_title, 
                InternetGatewayId=Ref(self.igw), 
                VpcId=Ref(self.vpc)))

        nat_instance_type = self.template.add_parameter(Parameter('natInstanceType', 
                Type='String',
                Default=str(network_config.get('nat_instance_type', 'm1.small')), 
                AllowedValues=self.strings['valid_instance_types'], 
                ConstraintDescription=self.strings['valid_instance_type_message'], 
                Description='Instance type to use when launching NAT instances.'))
        
        for x in range(0, max(int(network_config.get('public_subnet_count', 2)), int(network_config.get('private_subnet_count', 2)))):
            for y in network_config.get('subnet_types', ['public', 'private']):
                if y in self.template.mappings['networkAddresses']['subnet' + str(x)]:
                    if y not in self.local_subnets:
                        self.local_subnets[y] = {}
                    self.local_subnets[y][str(x)] = self.template.add_resource(ec2.Subnet(y + 'Subnet' + str(x), 
                            AvailabilityZone=FindInMap('RegionMap', Ref('AWS::Region'), 'az' + str(x) + 'Name'), 
                            VpcId=Ref(self.vpc), 
                            CidrBlock=FindInMap('networkAddresses', 'subnet' + str(x), y)))
                    route_table = self.template.add_resource(ec2.RouteTable(y + 'Subnet' + str(x) + 'RouteTable', 
                            VpcId=Ref(self.vpc)))
                    if y == 'public':
                        self.template.add_resource(ec2.Route(y + 'Subnet' + str(x) + 'EgressRoute', 
                                DependsOn=[igw_title],
                                DestinationCidrBlock='0.0.0.0/0', 
                                GatewayId=Ref(self.igw), 
                                RouteTableId=Ref(route_table)))
                    elif y == 'private':
                        nat_instance = self.create_nat_instance(x, nat_instance_type, 'public')
                        self.template.add_resource(ec2.Route(y + 'Subnet' + str(x) + 'EgressRoute', 
                                DestinationCidrBlock='0.0.0.0/0', 
                                InstanceId=Ref(nat_instance), 
                                RouteTableId=Ref(route_table)))
                    self.template.add_resource(ec2.SubnetRouteTableAssociation(y + 'Subnet' + str(x) + 'EgressRouteTableAssociation', 
                            RouteTableId=Ref(route_table), 
                            SubnetId=Ref(self.local_subnets[y][str(x)])))

        self.manual_parameter_bindings['vpcCidr'] = FindInMap('networkAddresses', 'vpcBase', 'cidr')
        self.manual_parameter_bindings['vpcId'] = Ref(self.vpc)

        for x in self.local_subnets: 
            if x not in self.subnets:
                self.subnets[x] = []
            for y in self.local_subnets[x]:
                self.subnets[x].append(Ref(self.local_subnets[x][y]))

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
                        SubnetId=Ref(self.local_subnets[nat_subnet_type][str(nat_subnet_number)]))],
                SourceDestCheck=False))

    def add_network_cidr_mapping(self, 
        network_config):
        '''
        Method calculates and adds a CloudFormation mapping that is used to set VPC and Subnet CIDR blocks.  Calculated based on CIDR block sizes and additionally checks to ensure all network segments fit inside of the specified overall VPC CIDR
        @param network_config [dict] dictionary of values containing data for creating 
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
        base_cidr = cidr_info.network().to_tuple()[0] + '/' + str(cidr_info.to_tuple()[1])
        ret_val['vpcBase'] = {'cidr': base_cidr}
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

    def add_vpn_gateway(self, vpn_conf):
        if 'vpn_name' in vpn_conf: 
            vpn_name = vpn_conf.get('vpn_name')
        else:
            vpn_name = self.__class__.__name__ + 'Gateway'

        gateway = self.template.add_resource(ec2.VPNGateway('vpnGateway', 
            Type=vpn_conf.get('vpn_type', 'ipsec.1'), 
            Tags=[ec2.Tag(key='Name', value=vpn_name)]))

        gateway_connection = self.template.add_resource(ec2.VPNGatewayAttachment('vpnGatewayAttachment', 
            VpcId=Ref(self.vpc), 
            InternetGatewayId=Ref(self.igw),
            VpnGatewayId=Ref(gateway)))

if __name__ == '__main__':
    import json
    with open('config_args.json', 'r') as f:
        cmd_args = json.loads(f.read())
    test = NetworkBase(cmd_args)
    print test.to_json()
