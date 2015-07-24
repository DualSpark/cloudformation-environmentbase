from environmentbase.template import Template


class HaNat(Template):
    '''
    Adds a highly available NAT that also serves as an NTP server
    '''

    def __init__(self, asg_size=1, instance_type='t2.micro', install_ntp=False):
        '''
        Method initializes HA NAT in a given environment deployment
        @param asg_size [number] - Number of instances in the autoscaling group
        @param instance_type [string] - Type of instances in the autoscaling group
        @param install_ntp [boolean] - Toggle for installing NTP on the NAT instances
        '''
        self.asg_size = asg_size
        self.instance_type = instance_type
        self.install_ntp = install_ntp

        super(HaNat, self).__init__(template_name='HaNat')

    def build_hook(self):
        '''
        Hook to add tier-specific assets within the build stage of initializing this class.
        '''
        pass
