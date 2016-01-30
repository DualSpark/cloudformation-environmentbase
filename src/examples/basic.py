from environmentbase.networkbase import NetworkBase
from environmentbase.patterns.bastion import Bastion
from environmentbase.patterns.ha_cluster import HaCluster


class MyEnvClass(NetworkBase):
    """
    Class creates a VPC and common network components for the environment
    """

    def create_hook(self):

        # Do custom troposphere resource creation here
        super(MyEnvClass, self).create_hook()

        # Add a bastion host as a child stack in the environment
        self.add_child_template(Bastion())

        # Add a preconfigured ASG + ELB as a child stack in the environment
        self.add_child_template(HaCluster(
            name="MyCluster",
            min_size=2, max_size=3,
            instance_type='t2.micro'))


if __name__ == '__main__':
    MyEnvClass()
