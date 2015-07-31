# Base Environment Generator
![Build Status](https://ci.dualspark.com/api/badge/github.com/DualSpark/cloudformation-environmentbase/status.svg?branch=master)

This package is intended to provide a common and consistent environment design
for building out complex demo environments. It may be used as a Python module
directly, or via an included command line tool.

This python script will create a VPC that should deploy cleanly into any region
across the publicly available AWS regions.  The VPC network, by default, will
include:

* A public (/24) and a private subnet (/22) in two different Availability Zones
* A NAT instance per AZ
* A bastion host
* An S3 bucket configured to allow Amazon ELB (within the same region) and AWS
  CloudTrail to aggregate logs

There are a number of configuration options documented within the script itself
using [docopt](http://docopt.org). An overview of the general capabilities and
features is as follows:

* This script queries the AWS VPC API to ensure that the AZ's selected for
  deployment will allow subnets to be deployed to them (sometimes an issue in
  older accounts)
* Modify the base network CIDR block and subnet size and count via parameters
* Set prefixes for S3 key names for ELB and CloudTrail logging paths within the
  created bucket

Used from the command line, this will generate the network alone, but when used
as a Python module, it's a powerful building block to help generate the basic
structures for more complex environments in CloudFormation very easily.

## Python Usage

The Python class EnvironmentBase is designed to be useful from command line
tools, but has further utility as a base class for more complicated
environments. The original aim of this script was to build a reusable artifact
that could serve as the common networking design for multi-AZ, multi-subnet
demo environments. As such, the environmentbase.py script contains a number of
methods that are meant to be used by sub-classes and provide abstractions for
common workflows and use cases. To use this class, simply place it in your
project folder (this is not yet packaged in a deployable artifact). The import
and class definition for your class will look similar to the following:

```python
from environmentbase.networkbase import NetworkBase

class MyEnvClass(NetworkBase):
    '''
    Class creates a VPC and common network components for the environment
    '''

    def create_action(self):

        self.initialize_template()
        self.construct_network()

        # Do custom troposphere resource creation here

        self.write_template_to_file()


    def deploy_action(self):

        # Do custom deploy steps here

        super(MyEnvClass, self).deploy_action()


if __name__ == '__main__':

    MyEnvClass()
```

See [example usage](docs/usage.rst) for a brief example of a subclass with overridden create and deploy methods.

Documentation within the class takes a modified usage of the
[doxygen](http://www.stack.nl/~dimitri/doxygen/manual/docblocks.html#pythonblocks)
standard by adding a @classarg identifier that indicates that a given method
utilizes an argument that's passed in via the class constructor along with the
type and description of that parameter.

## Command Line Usage

To use this script, you must install some requirements (listed  [here](https://github.com/DualSpark/cloudformation-environmentbase/blob/master/setup.py#L64))

This can be done by running the following command from this directory:

```bash
sudo pip install -e .
```

To use the script itself, you can run it directly from the command line:

```bash
python setup.py install
environmentbase --help
```

This script uses Docopt to collect and parse arguments per the help
documentation included. You do have the option of either passing in
a configuration file representing these values or using the command line
arguments to set them individually. A sample (with defaults) of the
configuration file is located in the config.json file. To run this, you
must have a boto.cfg file set with the credentials for the target account where
you'd like to deploy the CloudFormation template to.

The IAM permissions required to perform the VPC lookups are the following:

```javascript
{"Statement": [ {"Action": ["ec2:DescribeAvailabilityZones",
"ec2:DescribeRegions"], "Effect": "Allow", "Resource": "*" }]}
```

Once you have either set up your boto.cfg file, you can run the process as follows:

```bash
environmentbase create
```

This first run will provide you a copy of config.json.  If you want to use a different filename use 
```bash
environmentbase create --config-file config_args.json 
```

## File Descriptions

### ami_cache.json

This file is a simple JSON dictionary to use when assembling the 'RegionMap'
object within the CloudFormation template. This is a simple way to abstract AMI
Id's out of the solution and can be used in conjunction with Packer to populate
custom AMIs directly from external tools.

### config.json

This is a sample 'config' file to be passed into the command line use case and
identifies a simple dictionary of configuration keys and values to be used
within the execution of the script.

### environmentbase.py

This is the actual python script and can be used as a stand-alone class or as
a command line tool. Documentation is in
pseudo-[doxygen](http://www.stack.nl/~dimitri/doxygen/manual/docblocks.html#pythonblocks)
format and the command line argument parsing process is implemented in Docopt.
This script requires that all packages in the requirements.txt file are
installed.

### networkbase.py

This python script extends the functionlity of environmentbase.py to include all of the basic
network infrastructure including VPC, public and private subnets, NAT instances, and security groups.
The basic usage example shows how to use the class with your own code.

### environmentbase.template

This is a sample output from the environmentbase.py script run from the command
line with all arguments set to their defaults.

### EnvironmentBase.docx

A Microsoft Word-formatted version of this documentation.

### README.md

This file--documentation for usage of this set of scripts and files.

