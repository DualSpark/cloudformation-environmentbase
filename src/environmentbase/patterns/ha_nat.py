from environmentbase.template import Template
from environmentbase import resources
from troposphere import Ref, Join, Base64, FindInMap
from troposphere.ec2 import SecurityGroup, SecurityGroupIngress, SecurityGroupEgress
from troposphere.autoscaling import AutoScalingGroup, LaunchConfiguration, Tag
from troposphere.iam import Policy, Role, InstanceProfile


class HaNat(Template):
    '''
    Adds a highly available NAT that also serves as an NTP server
    Creates a 1-1 autoscaling group in the provided public subnet and creates
    a route directing egress traffic from the private subnet through this NAT
    '''

    def __init__(self, subnet_index, instance_type='t2.micro', enable_ntp=False, name='HaNat'):
        '''
        Method initializes HA NAT in a given environment deployment
        @param subnet_index [int] ID of the subnet that the NAT instance will be deployed to
        @param instance_type [string] - Type of NAT instance in the autoscaling group
        '''
        self.subnet_index = subnet_index
        self.instance_type = instance_type
        self.enable_ntp = enable_ntp

        # These will be initialized and consumed by various functions called in the build hook
        self.sg = None
        self.instance_profile = None

        super(HaNat, self).__init__(template_name=name)

    def build_hook(self):
        '''
        Hook to add tier-specific assets within the build stage of initializing this class.
        '''
        self.add_nat_sg()
        self.add_nat_instance_profile()
        self.add_nat_asg()

    def add_nat_sg(self):
        '''
        Create the NAT security group and add the ingress/egress rules
        '''
        self.sg = self.add_resource(SecurityGroup(
            "Nat%sSG" % str(self.subnet_index),
            VpcId=Ref(self.vpc_id),
            GroupDescription="Security group for NAT host."
        ))
        self.add_nat_sg_rules()

    def add_nat_sg_rules(self):
        '''
        Add the security group rules necessary for the NAT to operate
        For now, this is opening all ingress from the VPC and all egress to the internet
        '''
        self.add_resource(SecurityGroupIngress(
            "Nat%sIngress" % str(self.subnet_index),
            ToPort="-1",
            FromPort="-1",
            IpProtocol="-1",
            GroupId=Ref(self.sg),
            CidrIp=self.vpc_cidr
        ))
        self.add_resource(SecurityGroupEgress(
            "Nat%sEgress" % str(self.subnet_index),
            ToPort="-1",
            FromPort="-1",
            IpProtocol="-1",
            GroupId=Ref(self.sg),
            CidrIp='0.0.0.0/0'
        ))

    def add_nat_instance_profile(self):
        '''
        Create the NAT role and instance profile
        '''
        nat_role = self.add_resource(Role(
            "Nat%sRole" % str(self.subnet_index),
            AssumeRolePolicyDocument={
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {
                        "Service": ["ec2.amazonaws.com"]
                    },
                    "Action": ["sts:AssumeRole"]
                 }]
            },
            Path="/",
            Policies=[Policy(
                PolicyName="NAT%sPolicy" % str(self.subnet_index),
                PolicyDocument={
                    "Statement": [{
                        "Effect": "Allow",
                        "Action": [
                            "ec2:DescribeInstances",
                            "ec2:ModifyInstanceAttribute",
                            "ec2:DescribeSubnets",
                            "ec2:DescribeRouteTables",
                            "ec2:CreateRoute",
                            "ec2:ReplaceRoute",
                            "ec2:StartInstances",
                            "ec2:StopInstances"
                        ],
                        "Resource": "*"
                    }]
                }
            )]
        ))

        self.instance_profile = self.add_resource(InstanceProfile(
            "Nat%sInstanceProfile" % str(self.subnet_index),
            Path="/",
            Roles=[Ref(nat_role)]
        ))

    def add_nat_asg(self):

        nat_launch_config = self.add_resource(LaunchConfiguration(
            "Nat%sLaunchConfig" % str(self.subnet_index),
            UserData=Base64(resources.get_resource('nat_ntp_takeover.sh')),
            ImageId=FindInMap('RegionMap', Ref('AWS::Region'), 'natAmiId'),
            KeyName=Ref('ec2Key'),
            SecurityGroups=[Ref(self.sg)],
            EbsOptimized=False,
            IamInstanceProfile=Ref(self.instance_profile),
            InstanceType=self.instance_type,
            AssociatePublicIpAddress=True
        ))

        nat_asg = self.add_resource(AutoScalingGroup(
            "Nat%sASG" % str(self.subnet_index),
            DesiredCapacity=1,
            Tags=[
                Tag("Name", Join("-", [Ref(self.vpc_id), "NAT"]), True),
                Tag("isNat", "true", True)
            ],
            MinSize=1,
            MaxSize=1,
            Cooldown="30",
            LaunchConfigurationName=Ref(nat_launch_config),
            HealthCheckGracePeriod=30,
            HealthCheckType="EC2",
            VPCZoneIdentifier=[Ref(self.subnets['public'][self.subnet_index])]
        ))

        return nat_asg
