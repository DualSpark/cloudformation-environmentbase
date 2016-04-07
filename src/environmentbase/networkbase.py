from troposphere import GetAtt
from environmentbase import EnvironmentBase

from patterns.base_network import BaseNetwork


class NetworkBase(EnvironmentBase):
    """
    EnvironmentBase controller containing a root template with all of the base networking infrastructure
    for a common deployment within AWS. This is intended to be the 'base' stack for deploying child stacks
    """

    def add_config_hook(self):
        super(NetworkBase, self).add_config_hook()
        self._add_config_handler(BaseNetwork)

    def create_hook(self):
        super(NetworkBase, self).create_hook()

        network_config = self.config.get('network', {})
        nat_config = self.config.get('nat')

        base_network_template = BaseNetwork('BaseNetwork', network_config, nat_config)
        self.add_child_template(base_network_template)

        self.template._subnets = base_network_template._subnets.copy()
        self.template._vpc_id = GetAtt(base_network_template.name, 'Outputs.vpcId')

