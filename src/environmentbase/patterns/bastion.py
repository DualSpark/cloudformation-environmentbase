from environmentbase.template import Template
from troposphere import Ref, Output, GetAtt, ec2

SSH_PORT = '22'


class Bastion(Template):
    """
    Adds a bastion host within a given deployment based on environemntbase.
    """

    def __init__(self, name='bastion', ingress_port='2222', access_cidr='0.0.0.0/0'):
        """
        Method initializes bastion host in a given environment deployment
        @param name [string] - name of the tier to assign
        @param ingress_port [number] - port to allow ingress on. Must be a valid ELB ingress port. More info here: http://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-properties-ec2-elb-listener.html
        @param access_cidr [string] - CIDR notation for external access to this tier.
        """

        self.name = name
        self.ingress_port = ingress_port
        self.access_cidr = access_cidr

        super(Bastion, self).__init__(template_name=name)

    # Called after add_child_template() has attached common parameters and some instance attributes:
    # - RegionMap: Region to AMI map, allows template to be deployed in different regions without updating AMI ids
    # - ec2Key: keyname to use for ssh authentication
    # - vpcCidr: IP block claimed by whole VPC
    # - vpcId: resource id of VPC
    # - commonSecurityGroup: sg identifier for common allowed ports (22 in from VPC)
    # - utilityBucket: S3 bucket name used to send logs to
    # - availabilityZone[0-3]: Indexed names of AZs VPC is deployed to
    # - [public|private]Subnet[0-9]: indexed and classified subnet identifiers
    #
    # and some instance attributes referencing the attached parameters:
    # - self.vpc_cidr
    # - self.vpc_id
    # - self.common_security_group
    # - self.utility_bucket
    # - self.subnets: keyed by type and index (e.g. self.subnets['public'][1])
    # - self.azs: List of parameter references
    def build_hook(self):
        """
        Hook to add tier-specific assets within the build stage of initializing this class.
        """
        security_groups = self.add_security_groups()

        bastion_elb = self.add_elb(
            resource_name=self.name,
            security_groups=[security_groups['elb']],
            ports={self.ingress_port: SSH_PORT},
            utility_bucket=Ref(self.utility_bucket)
        )

        bastion_asg = self.add_asg(
            layer_name=self.name,
            security_groups=[security_groups['bastion'], self.common_security_group],
            load_balancer=bastion_elb
        )

        self.add_output(Output(
            'BastionELBDNSName',
            Value=GetAtt(bastion_elb, 'DNSName')
        ))

    @staticmethod
    def get_factory_defaults():
        return {"bastion": {"instance_type_default": "t2.micro"}}

    @staticmethod
    def get_config_schema():
        return {"bastion": {"instance_type_default": "str"}}

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
                VpcId=Ref(self.vpc_id),
                SecurityGroupIngress=[elb_sg_ingress_rule])
        )

        bastion_sg_name = '%sSecurityGroup' % self.name
        bastion_sg = self.add_resource(
            ec2.SecurityGroup(
                bastion_sg_name,
                GroupDescription='Security group for %s' % self.name,
                VpcId=Ref(self.vpc_id))
        )

        self.create_reciprocal_sg(
            elb_sg, elb_sg_name,
            bastion_sg, bastion_sg_name,
            from_port=SSH_PORT)

        return {'bastion': bastion_sg, 'elb': elb_sg}
