from troposphere import Template, Select, Ref, Parameter, FindInMap, Output, Base64, Join, GetAtt
from environmentbase import EnvironmentBase


class ChildTemplateBase(EnvironmentBase):
    """
    Base class to manage common input parameters for non-network templates.
    """

    def setup_parameters(self):
        self.vpc_cidr = self.template.add_parameter(Parameter('vpcCidr',
            Description='CIDR of the VPC network',
            Type='String',
            AllowedPattern=self.strings['cidr_regex'],
            ConstraintDescription=self.strings['cidr_regex_message']))

        self.vpc_id = self.template.add_parameter(Parameter('vpcId',
            Description='ID of the VPC network',
            Type='String'))

        self.common_security_group = self.template.add_parameter(Parameter('commonSecurityGroup',
            Description='Security Group ID of the common security group for this environment',
            Type='String'))

        self.utility_bucket = self.template.add_parameter(Parameter('utilityBucket',
            Description='Name of the S3 bucket used for infrastructure utility',
            Type='String'))

        network_config = self.config.get('network', {})

        for y in ['public', 'private']:
            if y not in self.subnets:
                self.subnets[y] = []
            for x in range(0, max(int(network_config.get('public_subnet_count', 2)), int(network_config.get('private_subnet_count', 2)))):
                self.subnets[y].append(Ref(self.template.add_parameter(Parameter(y.lower() + 'Subnet' + str(x),
                    Description='Private subnet ' + str(x),
                    Type='String'))))

        self.azs = []

        for x in range(0, max(int(network_config.get('public_subnet_count', 2)), int(network_config.get('private_subnet_count', 2)))):
            self.azs.append(Ref(self.template.add_parameter(Parameter('availabilityZone' + str(x),
                Description='Availability Zone ' + str(x),
                Type='String'))))

    def create_action(self):
        """
        Create_action method manages creation of common parameters for derived classes
        """
        self.initialize_template()
        self.setup_parameters()
        self.write_tempate_to_file()
