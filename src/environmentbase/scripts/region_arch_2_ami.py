__author__ = 'Eric Price'

from datetime import date
import boto3
import json
import re, sys

VOL_TYPE_MAG = 'standard'
VOL_TYPE_SSD = 'gp2'

VIRT_TYPE_PV = 'paravirtual'
VIRT_TYPE_HVM = 'hvm'

ROOT_DEV_EBS = 'ebs'
ROOT_DEV_INS = 'instance-store'

YEAR = date.today().year

#  amzn-ami-hvm-2015.03.rc-0.x86_64-gp2
RC_REGEX = re.compile(r'rc-[0-9]')

# amzn-ami-vpc-nat-hvm-2014.03.2.x86_64-gp2
NAT_REGEX = re.compile(r'-vpc-nat-')

# amzn-ami-minimal-pv-2014.03.2.x86_64-s3
MINIMAL_REGEX = re.compile(r'-minimal-')

# aws ec2 describe-images --owners amazon --filters Name=name,Values="amzn-ami-hvm-2015*-gp2"


def get_region_list():
    client = boto3.client('ec2')
    response = client.describe_regions()

    return_code = response['ResponseMetadata']['HTTPStatusCode']
    if return_code != 200:
        raise Exception('describe_regions returned %s' % return_code)
    else:
        return map(lambda entry: entry['RegionName'], response['Regions'])


def find_amis(region='us-west-2', year=YEAR):
    client = boto3.client('ec2', region_name=region)

    ami_name = 'amzn-ami-*-%s.*.x86_64-*' % year
    filter_name = [
        {'Name': 'name', 'Values': [ami_name]},
        {'Name': 'root-device-type', 'Values': [ROOT_DEV_EBS]},  # no instance-store images
        {'Name': 'virtualization-type', 'Values': [VIRT_TYPE_PV, VIRT_TYPE_HVM]},
    ]
    response = client.describe_images(
        Owners=['amazon'],
        Filters=filter_name)

    return_code = response['ResponseMetadata']['HTTPStatusCode']
    if return_code != 200:
        raise Exception('describe_image returned %s' % return_code)
    else:
        return response['Images']


def filter_amis(ami_list, remove_rcs=True, use_nat_instances=False, remove_minimal=True):
    #  Remove release candidates
    if remove_rcs:
        ami_list = filter(lambda ami: RC_REGEX.search(ami['Name']) is None, ami_list)

    # Looking for NAT instances?
    if use_nat_instances:
        nat_filter_fun = lambda ami: NAT_REGEX.search(ami['Name']) is not None
    else:
        nat_filter_fun = lambda ami: NAT_REGEX.search(ami['Name']) is None
    ami_list = filter(nat_filter_fun, ami_list)

    # Filter out 'minimal' instance types
    if remove_minimal:
        ami_list = filter(lambda ami: MINIMAL_REGEX.search(ami['Name']) is None, ami_list)

    if len(ami_list) == 0:
        return None

    ami_list = sorted(ami_list, key=lambda ami: ami['CreationDate'], reverse=True)

    return ami_list


def get_hvm_ami(filtered_amis, vol_type=VOL_TYPE_SSD):
    def filter_fun(ami):
        virt_match = ami['VirtualizationType'] == VIRT_TYPE_HVM
        vol_match = ami['BlockDeviceMappings'][0]['Ebs']['VolumeType'] == vol_type
        return virt_match and vol_match

    amis = filter(filter_fun, filtered_amis)
    return None if not amis else filtered_amis[0]


def get_pv_ami(filtered_amis):
    def filter_fun(ami):
        virt_match = ami['VirtualizationType'] == VIRT_TYPE_PV
        vol_match = ami['BlockDeviceMappings'][0]['Ebs']['VolumeType'] == VOL_TYPE_MAG
        return virt_match and vol_match

    amis = filter(filter_fun, filtered_amis)
    return None if not amis else filtered_amis[0]


if __name__ == '__main__':
    ami_map = {'PV64': {}, 'HVM64': {}}
    regions = get_region_list()

    print 'Regions %s' % regions
    for region in regions:
        print '\n-------------------\nProcessing %s' % region
        images = find_amis(region, YEAR)
        filtered_amis = filter_amis(images)

        # If no hits retry with last years AMIs
        if filtered_amis is None:
            images = find_amis(region, YEAR-1)
            filtered_amis = filter_amis(images)

        hvm_ami = get_hvm_ami(filtered_amis)
        if hvm_ami:
            ami_map['HVM64'][region] = hvm_ami["ImageId"]
            print json.dumps(hvm_ami, indent=4, separators=(',', ': '))
        else:
            print '* No HVM hits for region', region

        pv_ami = get_pv_ami(filtered_amis)
        if pv_ami:
            ami_map['PV64'][region] = pv_ami["ImageId"]
            print json.dumps(pv_ami, indent=4, separators=(',', ': '))
        else:
            print '* No PV hits for region', region

    json_str = json.dumps(ami_map, indent=4, separators=(',', ': '), sort_keys=True)

    # Someone please explain to me why I have to do this repeatedly for all matches to be replaced!!
    import re
    for _ in range(7):
        json_str = re.sub(r": \{\n\s*([^\n]+),\n\s*([^\n]+)\n\s*\}", r": { \1, \2 }", json_str, re.MULTILINE)

    print '\n========================'
    print json_str
