from environmentbase.template import Template
from troposphere import Ref, ec2

SSH_PORT = '22'


class Bastion(Template):

    def __init__(self, name='bastion', ingress_port='2222', access_cidr='0.0.0.0/0'):

        self.name = name
        self.ingress_port = ingress_port
        self.access_cidr = access_cidr

        super(Bastion, self).__init__(template_name=name)

    def build_hook(self):

        security_groups = self.add_security_groups()

        bastion_elb = self.add_elb(
            resource_name=self.name,
            security_groups=[security_groups['elb']],
            ports={self.ingress_port: SSH_PORT}
        )

        bastion_asg = self.add_asg(
            layer_name=self.name,
            security_groups=[security_groups['bastion']],
            load_balancer=bastion_elb
        )

    def add_security_groups(self):

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
