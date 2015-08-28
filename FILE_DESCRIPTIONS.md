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

