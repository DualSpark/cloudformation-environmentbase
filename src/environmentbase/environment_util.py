#!/usr/bin/env python
'''environment_util.py
Utility tool helps to manage mappings and gathering data from across multiple AWS Availability zones.

Usage:
    environment_util ami-map <AMI_NAME> [--aws_region <AWS_REGION>]

Options:
  -h --help                  Show this screen.
  -v --version               Show version.
  --aws_region <AWS_REGION>  Region to start queries to AWS API from [default: us-east-1].
'''
from docopt import Docopt
import boto

if __name__ == '__main__':
    arguments = docopt(__doc__, version='environmentbase-cfn environment_util 0.1')

    if arguments.get('ami-map'):
        region_map = {}
        v = boto.connect_vpc(arguments.get('--aws_region', 'us-east-1'))
        for region in v.get_all_regions():
            if region.name not in region_map.keys():
                region_map[region.name] = {}
            c = boto.connect_ec2(region.name)
            for k, v in image_names:
                images = c.get_all_images(filters={'name': v})
                if len(images) == 0:
                    # no images found...
                    pass
                elif len(images) > 1:
                    # too many images found - filters not tight enough
                    pass
                else:
                    region_map[region.name][k] = images[0].id

