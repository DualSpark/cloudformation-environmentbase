from environmentbase.template import Template
from troposphere import Ref, Output, GetAtt, ec2

SSH_PORT = '22'


class Bastion(Template):
    """
    Adds a bastion host within a given deployment based on environemntbase.
    """

    def __init__(self,
                 name='bastion',
                 ingress_port=None,
                 access_cidr=None,
                 default_instance_type=None,
                 suggested_instance_types=None,
                 user_data=None):
        """
        Method initializes bastion host in a given environment deployment
        @param name [string] - name of the tier to assign
        @param ingress_port [number] - port to allow ingress on. Must be a valid ELB ingress port.
        More info here: http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-properties-ec2-elb-listener.html
        @param access_cidr [string] - CIDR notation for external access to this tier.
        @param user_data [string] - User data to to initialize the bastion hosts.
        @param default_instance_type [string] EC2 instance type name
        @param suggested_instance_types [list<string>] List of EC2 instance types available for selection in CloudFormation
        """

        self.name = name
        self.user_data = user_data

        # Let constructor parameters override runtime config settings
        cfg = self.runtime_config['bastion']
        self.ingress_port = ingress_port or cfg['ingress_port']
        self.access_cidr = access_cidr or cfg['remote_access_cidr']
        self.default_instance_type = default_instance_type or cfg['default_instance_type']
        self.suggested_instance_types = suggested_instance_types or cfg['suggested_instance_types']

        super(Bastion, self).__init__(template_name=name)

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
    # - self.subnets: keyed by type and index (e.g. self.subnets['public'][1])
    # - self.user_data: User data for launch configuration
    def build_hook(self):
        """
        Hook to add tier-specific assets within the build stage of initializing this class.
        """
        security_groups = self.add_security_groups()

        bastion_elb = self.add_elb(
            resource_name=self.name,
            security_groups=[security_groups['elb']],
            listeners=[{
                'elb_port': self.ingress_port,
                'instance_port': SSH_PORT
            }],
            utility_bucket=self.utility_bucket
        )

        self.add_asg(
            layer_name=self.name,
            security_groups=[security_groups['bastion'], self.common_security_group],
            load_balancer=bastion_elb,
            user_data=self.user_data,
            default_instance_type=self.default_instance_type,
            suggested_instance_types=self.suggested_instance_types
        )

        self.add_output(Output(
            'BastionELBDNSName',
            Value=GetAtt(bastion_elb, 'DNSName')
        ))

        self.add_output(Output(
            'BastionELBDNSZoneId',
            Value=GetAtt(bastion_elb, 'CanonicalHostedZoneNameID')
         ))

        self.add_output(Output(
            'BastionSecurityGroupId',
            Value=Ref(security_groups['bastion'])
        ))

    @staticmethod
    def get_factory_defaults():
        return {"bastion": {
            "default_instance_type": "t2.micro",
            "suggested_instance_types": [
                "m1.small", "t2.micro", "t2.small", "t2.medium",
                "m3.medium",
                "c3.large", "c3.2xlarge"
            ],
            "remote_access_cidr": "0.0.0.0/0",
            "ingress_port": 2222
        }}

    @staticmethod
    def get_config_schema():
        return {"bastion": {
            "default_instance_type": "str",
            "suggested_instance_types": "list",
            "remote_access_cidr": "str",
            "ingress_port": "int"
        }}

    def add_security_groups(self):
        """
        Wrapper method to encapsulate process of creating security groups for this tier.
        """

        elb_sg_ingress_rule = ec2.SecurityGroupRule(FromPort=self.ingress_port, ToPort=self.ingress_port, IpProtocol='tcp', CidrIp=self.access_cidr)

        elb_sg_name = '%sElbSecurityGroup' % self.name
        elb_sg = self.add_resource(
            ec2.SecurityGroup(
                elb_sg_name,
                GroupDescription='Security group for %s ELB' % self.name,
                VpcId=self.vpc_id,
                SecurityGroupIngress=[elb_sg_ingress_rule])
        )

        bastion_sg_name = '%sSecurityGroup' % self.name
        bastion_sg = self.add_resource(
            ec2.SecurityGroup(
                bastion_sg_name,
                GroupDescription='Security group for %s' % self.name,
                VpcId=self.vpc_id)
        )

        self.create_reciprocal_sg(
            elb_sg, elb_sg_name,
            bastion_sg, bastion_sg_name,
            from_port=SSH_PORT)

        return {'bastion': bastion_sg, 'elb': elb_sg}
