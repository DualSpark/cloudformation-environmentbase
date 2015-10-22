import json
from itertools import product, chain

from troposphere import Ref, Parameter, FindInMap
import troposphere.ec2 as ec2
import boto.vpc
import boto
from environmentbase import EnvironmentBase
from ipcalc import IP, Network
import resources as res
from patterns import ha_nat

import netaddr
from toolz import groupby, assoc


class NetworkBase(EnvironmentBase):
    """
    Class creates all of the base networking infrastructure for a common deployment within AWS
    This is intended to be the 'base' template for deploying child templates
    """

    def construct_network(self):
        """
        Main function to construct VPC, subnets, security groups, NAT instances, etc
        """
        network_config = self.config.get('network', {})

        self.template._azs = []

        az_count = int(network_config.get('az_count', '2'))

        self.stack_outputs = {}

        # Simple mapping of AZs to NATs, to prevent creating duplicates
        self.az_nat_mapping = {}
        
        self.add_vpc_az_mapping(boto_config=self.config.get('boto', {}), az_count=az_count)
        self.add_network_cidr_mapping(network_config=network_config)
        self.create_network_components(network_config=network_config)

        self.template._common_security_group = self.template.add_resource(ec2.SecurityGroup('commonSecurityGroup',
            GroupDescription='Security Group allows ingress and egress for common usage patterns throughout this deployed infrastructure.',
            VpcId=self.template.vpc_id,
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
            self.template._azs.append(FindInMap('RegionMap', Ref('AWS::Region'), 'az' + str(x) + 'Name'))

    def create_action(self):
        """
        Override EnvironmentBase.create_action() to construct VPC
        """
        self.load_config()
        self.initialize_template()
        self.construct_network()
        self.create_hook()
        self.serialize_templates()

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

    def create_network_components(self, network_config=None):
        """
        Method creates a network with the specified number of public and private subnets within the VPC cidr specified by the networkAddresses CloudFormation mapping
        @param network_config [dict] collection of network parameters for creating the VPC network
        """
        ## make VPC
        if 'network_name' in network_config:
            network_name = network_config.get('network_name')
        else:
            network_name = self.__class__.__name__

        self.template._vpc_id = self.template.add_resource(ec2.VPC('vpc',
                CidrBlock=FindInMap('networkAddresses', 'vpcBase', 'cidr'),
                EnableDnsSupport=True,
                EnableDnsHostnames=True,
                Tags=[ec2.Tag(key='Name', value=network_name)]))

        self.template._vpc_cidr = FindInMap('networkAddresses', 'vpcBase', 'cidr')

        self.template._igw = self.template.add_resource(ec2.InternetGateway('vpcIgw'))

        ## add IGW
        igw_title = 'igwVpcAttachment'
        self.template._vpc_gateway_attachment = self.template.add_resource(ec2.VPCGatewayAttachment(
            igw_title,
            InternetGatewayId=self.template.igw,
            VpcId=self.template.vpc_id))

        self.gateway_hook()

        ## make Subnets
        network_cidr_base = str(network_config.get('network_cidr_base', '172.16.0.0'))

        # Iterate through each subnet type for each AZ and add subnets, routing tables, routes, and NATs as necessary
        for index in range(int(network_config.get('az_count', 2))):
            az = index
            az_key = 'AZ{}'.format(az)

            for ind, subnet_config in enumerate(network_config.get('subnet_config', {})):
                subnet_type = subnet_config.get('type', 'private')
                subnet_layer = subnet_config.get('name')

                AvailabilityZone = FindInMap('RegionMap', Ref('AWS::Region'), 'az' + str(index) + 'Name')
                CidrBlock = self.template.mappings['networkAddresses'][az_key][subnet_layer]

                # Save the subnet references to the template object
                if subnet_type not in self.template._subnets:
                    self.template._subnets[subnet_type] = {}

                if subnet_layer not in self.template._subnets[subnet_type]:
                    self.template._subnets[subnet_type][subnet_layer] = []

                # Create the subnet
                subnet = self.template.add_resource(ec2.Subnet(
                    subnet_layer + 'AZ' + str(index),
                    AvailabilityZone=AvailabilityZone,
                    VpcId=self.template.vpc_id,
                    CidrBlock=CidrBlock,
                    Tags=[ec2.Tag(key='network', value=subnet_type), 
                          ec2.Tag(key='Name', value=' '.join([subnet_layer, 'AZ:', str(index)])),
                        ]))

                self.template._subnets[subnet_type][subnet_layer].append(subnet)

                # Create the routing table
                route_table = self.template.add_resource(ec2.RouteTable(
                    subnet_layer + 'AZ' + str(index) + 'RouteTable',
                    VpcId=self.template.vpc_id))

                # Create the NATs and egress rules
                self.create_subnet_egress(index, route_table, igw_title, subnet_type, subnet_layer)

                # Associate the routing table with the subnet
                self.template.add_resource(ec2.SubnetRouteTableAssociation(
                    subnet_layer + 'AZ' + str(index) + 'EgressRouteTableAssociation',
                    RouteTableId=Ref(route_table),
                    SubnetId=Ref(subnet)))

        self.template.manual_parameter_bindings['vpcId'] = self.template.vpc_id
        self.template.manual_parameter_bindings['vpcCidr'] = self.template.vpc_cidr
        self.template.manual_parameter_bindings['internetGateway'] = self.template.igw
        self.template.manual_parameter_bindings['igwVpcAttachment'] = self.template.vpc_gateway_attachment

    def create_subnet_egress(self, index, route_table, igw_title, subnet_type, subnet_layer):
        """
        Create an egress route for the a subnet with the given index and type
        Override to create egress routes for other subnet types
        Creates the NAT instances in the public subnets
        """

        # For public subnets, create the route to the IGW
        if subnet_type == 'public':
            self.template.add_resource(ec2.Route(subnet_layer + 'AZ' + str(index) + 'EgressRoute',
                DependsOn=[igw_title],
                DestinationCidrBlock='0.0.0.0/0',
                GatewayId=self.template.igw,
                RouteTableId=Ref(route_table)))

        # For private subnets, create a NAT instance in a public subnet in the same AZ
        elif subnet_type == 'private':

            # If we have already created a NAT in this AZ, skip it
            if self.az_nat_mapping.get(index):
                return

            nat_instance_type = self.config['nat']['instance_type']
            nat_enable_ntp = self.config['nat']['enable_ntp']
            extra_user_data = self.config['nat'].get('extra_user_data')
            ha_nat = self.create_nat(
                index,
                nat_instance_type,
                nat_enable_ntp,
                name='HaNat' + str(index),
                extra_user_data=extra_user_data)

            # We merge the NAT template into the root template
            self.template.merge(ha_nat)

            # Save the reference to the HA NAT, so we don't recreate it if we hit another private subnet in this AZ
            self.az_nat_mapping[index] = ha_nat


    def gateway_hook(self):
        """
        Override to allow subclasses to create VPGs and similar components during network creation
        """
        pass

    def create_nat(self, index, nat_instance_type, enable_ntp, name, extra_user_data=None):
        """
        Override to customize your NAT instance. The returned object must be a
        subclass of ha_nat.HaNat.
        """
        return ha_nat.HaNat(
            index,
            nat_instance_type,
            enable_ntp,
            name=name,
            extra_user_data=extra_user_data)


    def _get_subnet_config_w_az(self, network_config):
        az_count = int(network_config.get('az_count', 2))
        subnet_config = network_config.get('subnet_config', {})

        for subnet in subnet_config:
            for az in range(az_count):
                newsubnet = assoc(subnet, 'AZ', az)
                yield newsubnet

    def _get_subnet_config_w_cidr(self, network_config):
        network_cidr_base = str(network_config.get('network_cidr_base', '172.16.0.0'))
        network_cidr_size = str(network_config.get('network_cidr_size', '20'))
        first_network_address_block = str(network_config.get('first_network_address_block', network_cidr_base))

        ret_val = {}
        base_cidr = network_cidr_base + '/' + network_cidr_size
        net = netaddr.IPNetwork(base_cidr)
        
        grouped_subnet = groupby('size', self._get_subnet_config_w_az(network_config))
        subnet_groups = sorted(grouped_subnet.items())
        available_cidrs = []

        for subnet_size, subnet_configs in subnet_groups:
            newcidrs = net.subnet(int(subnet_size))

            for subnet_config in subnet_configs:
                try:
                    cidr = newcidrs.next()
                except StopIteration as e:
                    net = chain(*reversed(available_cidrs)).next()
                    newcidrs = net.subnet(int(subnet_size))
                    cidr = newcidrs.next()

                new_config = assoc(subnet_config, 'cidr', str(cidr))
                yield new_config
            else:
                net = newcidrs.next()
                available_cidrs.append(newcidrs)


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
        base_cidr = network_cidr_base + '/' + network_cidr_size
        cidr_info = Network(base_cidr)
        ret_val['vpcBase'] = {'cidr': base_cidr}
        current_base_address = first_network_address_block

        subnet_config = self._get_subnet_config_w_cidr(network_config)
        subnet_config = list(subnet_config)

        for index, subnet_config in enumerate(subnet_config):
            subnet_type = subnet_config.get('type', 'private')
            subnet_size = subnet_config.get('size', '22')
            subnet_name = subnet_config.get('name', 'subnet')
            subnet_az = subnet_config.get('AZ', '-1')
            subnet_cidr = subnet_config.get('cidr', 'ERROR')
            az_key = 'AZ{}'.format(subnet_az)
            
            # TODO: check for subnet collisions

            if az_key not in ret_val:
                ret_val[az_key] = dict()
            if subnet_name not in ret_val[az_key]:
                ret_val[az_key][subnet_name] = dict()
            ret_val[az_key][subnet_name] = subnet_cidr

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
            VpcId=self.template.vpc_id,
            InternetGatewayId=self.template.igw,
            VpnGatewayId=gateway))
