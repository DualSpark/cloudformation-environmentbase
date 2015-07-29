from troposphere import Template, Select, Ref, Parameter, FindInMap, Output, Base64, Join, GetAtt, Retain
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
import resources as res


class NetworkBase(EnvironmentBase):
    """
    Class creates all of the base networking infrastructure for a common deployment within AWS
    This is intended to be the 'base' template for deploying child templates
    """

    def construct_network(self):
        '''
        Main function to construct VPC, subnets, security groups, NAT instances, etc
        '''
        network_config = self.config.get('network', {})
        template_config = self.config.get('template', {})

        self.vpc = None
        self.azs = []

        az_count = int(network_config.get('az_count', '2'))

        self.local_subnets = {}
        self.stack_outputs = {}
        self.add_vpc_az_mapping(boto_config=self.config.get('boto', {}), az_count=az_count)
        self.add_network_cidr_mapping(network_config=network_config)
        self.create_network(network_config=network_config)

        self.template.add_utility_bucket(
            name=template_config.get('s3_utility_bucket', 'demo'),
            param_binding_map=self.manual_parameter_bindings)

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

        for x in range(0, az_count):
            self.azs.append(FindInMap('RegionMap', Ref('AWS::Region'), 'az' + str(x) + 'Name'))

    def create_action(self):
        '''
        Override EnvironmentBase.create_action() to construct VPC
        '''
        self.initialize_template()
        self.construct_network()
        self.write_template_to_file()

    def add_vpc_az_mapping(self,
                           boto_config,
                           az_count=2):
        """
        Method gets the AZs within the given account where subnets can be created/deployed
        This is necessary due to some accounts having 4 subnets available within ec2 classic and only 3 within vpc
        which causes the Select by index method of picking azs unpredictable for all accounts
        @param boto_config [dict] collection of boto configuration values as set by the configuration file
        @param az_count [int] number of AWS availability zones to include in the VPC mapping
        """
        az_dict = {}
        region_list = []
        aws_auth_info = {}
        if 'aws_access_key_id' in boto_config and 'aws_secret_access_key' in boto_config:
            aws_auth_info['aws_access_key_id'] = boto_config.get('aws_access_key_id')
            aws_auth_info['aws_secret_access_key'] = boto_config.get('aws_secret_access_key')
        conn = boto.vpc.connect_to_region(region_name=boto_config.get('region_name', 'us-east-1'), **aws_auth_info)
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
                        self.template.add_region_map_value(region.name, item, temp_dict[item])

    def create_network(self,
                       network_config=None):
        """
        Method creates a network with the specified number of public and private subnets within the VPC cidr specified by the networkAddresses CloudFormation mapping
        @param network_config [dict] collection of network parameters for creating the VPC network
        """
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
                Default=str(network_config.get('nat_instance_type', 't2.small')),
                AllowedValues=res.get_str('valid_instance_types'),
                ConstraintDescription=res.get_str('valid_instance_type_message'),
                Description='Instance type to use when launching NAT instances.'))

        subnet_types = network_config.get('subnet_types',['public','private'])

        self.gateway_hook()

        for index in range(0, int(network_config.get('az_count', 2))):
            for subnet_type in subnet_types:
                if subnet_type in self.template.mappings['networkAddresses']['subnet' + str(index)]:
                    if subnet_type not in self.local_subnets:
                        self.local_subnets[subnet_type] = {}
                    self.local_subnets[subnet_type][str(index)] = self.template.add_resource(ec2.Subnet(subnet_type + 'Subnet' + str(index),
                            AvailabilityZone=FindInMap('RegionMap', Ref('AWS::Region'), 'az' + str(index) + 'Name'),
                            VpcId=Ref(self.vpc),
                            CidrBlock=FindInMap('networkAddresses', 'subnet' + str(index), subnet_type)))

        for index in range(0, int(network_config.get('az_count', 2))):
            for subnet_type in subnet_types:
                if subnet_type in self.template.mappings['networkAddresses']['subnet' + str(index)]:

                    route_table = self.template.add_resource(ec2.RouteTable(subnet_type + 'Subnet' + str(index) + 'RouteTable',
                            VpcId=Ref(self.vpc)))

                    self.create_subnet_egress(index, route_table, igw_title, nat_instance_type, subnet_type)

                    self.template.add_resource(ec2.SubnetRouteTableAssociation(subnet_type + 'Subnet' + str(index) + 'EgressRouteTableAssociation',
                            RouteTableId=Ref(route_table),
                            SubnetId=Ref(self.local_subnets[subnet_type][str(index)])))

        self.manual_parameter_bindings['vpcCidr'] = FindInMap('networkAddresses', 'vpcBase', 'cidr')
        self.manual_parameter_bindings['vpcId'] = Ref(self.vpc)

        for x in self.local_subnets:
            if x not in self.subnets:
                self.subnets[x] = []
            for y in self.local_subnets[x]:
                self.subnets[x].append(Ref(self.local_subnets[x][y]))

    def create_subnet_egress(self,
                      index,
                      route_table,
                      igw_title,
                      nat_instance_type,
                      subnet_type):
        """
        Create an egress route for the a subnet with the given index and type
        Override to create egress routes for other subnet types
        """

        if subnet_type == 'public':
            self.template.add_resource(ec2.Route(subnet_type + 'Subnet' + str(index) + 'EgressRoute',
                DependsOn=[igw_title],
                DestinationCidrBlock='0.0.0.0/0',
                GatewayId=Ref(self.igw),
                RouteTableId=Ref(route_table)))
        elif subnet_type == 'private':
            # private subnets need a NAT instance in a public subnet
           nat_instance = self.create_nat_instance(index, nat_instance_type, subnet_type)
           self.template.add_resource(ec2.Route(subnet_type + 'Subnet' + str(index) + 'EgressRoute',
            DestinationCidrBlock='0.0.0.0/0',
            InstanceId=Ref(nat_instance),
            RouteTableId=Ref(route_table)))
        # else:
        #     self.custom_egress(index, route_table, igw_title, nat_instance_type, subnet_type)

    def gateway_hook(self):
        """
        Override to allow subclasses to create VPGs and similar components during network creation
        """
        pass


    def create_nat_instance(self,
                            nat_subnet_number,
                            nat_instance_type=None,
                            nat_subnet_type='public'):
        """
        Method creates a NAT instance for the private subnet within the specified corresponding subnet
        @param nat_subnet_number [int] ID of the subnet that the NAT instance will be deployed to
        @param nat_instance_type [string | Troposphere.Parameter] instance type to be set when launching the NAT instance
        @param nat_subnet_type [string] type of subnet (public/private) that this instance will be deployed for (which subnet is going to use this to egress traffic)
        """
        # if nat_subnet_type == 'public':
        #     source_name = 'private'
        # else:
        #     source_name = 'public'

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
                            CidrIp=FindInMap('networkAddresses', 'subnet' + str(nat_subnet_number), nat_subnet_type))],
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
                Tags=[ec2.Tag('Name','NAT')],
                NetworkInterfaces=[ec2.NetworkInterfaceProperty(
                        AssociatePublicIpAddress=True,
                        DeleteOnTermination=True,
                        DeviceIndex='0',
                        GroupSet=[Ref(nat_sg)],
                        SubnetId=Ref(self.local_subnets['public'][str(nat_subnet_number)]))],
                SourceDestCheck=False))

    def add_network_cidr_mapping(self,
                                 network_config):
        """
        Method calculates and adds a CloudFormation mapping that is used to set VPC and Subnet CIDR blocks.  Calculated based on CIDR block sizes and additionally checks to ensure all network segments fit inside of the specified overall VPC CIDR
        @param network_config [dict] dictionary of values containing data for creating
        """
        az_count = int(network_config.get('az_count', '2'))
        network_cidr_base = str(network_config.get('network_cidr_base', '172.16.0.0'))
        network_cidr_size = str(network_config.get('network_cidr_size', '20'))
        first_network_address_block = str(network_config.get('first_network_address_block', network_cidr_base))

        ret_val = {}
        cidr_info = Network(network_cidr_base + '/' + network_cidr_size)
        base_cidr = cidr_info.network().to_tuple()[0] + '/' + str(cidr_info.to_tuple()[1])
        ret_val['vpcBase'] = {'cidr': base_cidr}
        current_base_address = first_network_address_block

        subnet_types = network_config.get('subnet_types',['public','private'])

        for index in range(0, len(subnet_types)):
            subnet_type = subnet_types[index]
            subnet_size = network_config.get(subnet_type + '_subnet_size','22')

            if index != 0:
                range_reset = Network(current_base_address + '/' + str(subnet_size))
                current_base_address = IP(int(range_reset.host_last().hex(), 16) + 2).to_tuple()[0]

            for subnet_id in range(0, az_count):
                if not cidr_info.check_collision(current_base_address):
                    raise RuntimeError('Cannot continue creating network--current base address is outside the range of the master Cidr block. Found on pass ' + str(index + 1) + ' when creating ' + subnet_type + ' subnet cidrs')
                ip_info = Network(current_base_address + '/' + str(subnet_size))
                range_info = ip_info.network().to_tuple()
                if 'subnet' + str(subnet_id) not in ret_val:
                    ret_val['subnet' + str(subnet_id)] = dict()
                ret_val['subnet' + str(subnet_id)][subnet_type] = ip_info.network().to_tuple()[0] + '/' + str(ip_info.to_tuple()[1])
                current_base_address = IP(int(ip_info.host_last().hex(), 16) + 2).to_tuple()[0]

        return self.template.add_mapping('networkAddresses', ret_val)

    def add_vpn_gateway(self,
                        vpn_conf):
        """
        Not surprisingly, adds a VPN gateway to the network created by this template.
        @param vpn_conf [dict] - collection of vpn-level configuration values.
        """
        if 'vpn_name' in vpn_conf:
            vpn_name = vpn_conf.get('vpn_name')
        else:
            vpn_name = self.__class__.__name__ + 'Gateway'

        gateway = self.template.add_resource(ec2.VPNGateway('vpnGateway',
            Type=vpn_conf.get('vpn_type', 'ipsec.1'),
            Tags=[ec2.Tag(key='Name', value=vpn_name)]))

        gateway_connection = self.template.add_resource(ec2.VPCGatewayAttachment('vpnGatewayAttachment',
            VpcId=Ref(self.vpc),
            InternetGatewayId=Ref(self.igw),
            VpnGatewayId=Ref(gateway)))

if __name__ == '__main__':
    import json
    with open('config.json', 'r') as f:
        cmd_args = json.loads(f.read())
    test = NetworkBase(cmd_args)
    print test.to_json()
