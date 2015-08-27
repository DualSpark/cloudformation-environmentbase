awscmd="/usr/bin/aws"

# Need jq to parse and modify json
yum install -y jq

# Get the region of this instance
II_URI="http://169.254.169.254/latest/dynamic/instance-identity/document"
REGION=$(curl --retry 3 --retry-delay 0 --silent --fail $II_URI | grep region | awk -F\" '{print $4}')

# Set default AWS CLI region and output type
export AWS_DEFAULT_REGION=$REGION
export AWS_DEFAULT_OUTPUT="text"

# Get the current instance ID and VPC ID
INSTANCE_ID=$(curl --retry 3 --retry-delay 0 --silent --fail $II_URI | grep instanceId | awk -F\" '{print $4}')
VPC_ID=$(${awscmd} ec2 describe-instances --instance-ids $INSTANCE_ID --query 'Reservations[*].Instances[*].VpcId')

# Grab the private IP addresses of all instances with the NAT tag
NTP_IPS=$(${awscmd} ec2 describe-instances --filter Name=tag:isNat,Values=true --query 'Reservations[*].Instances[*].PrivateIpAddress')

# Reformat as JSON list of strings and insert into DHCP Options payload format
NTP_IPS=$(echo $NTP_IPS | sed 's/ /\",\"/g;s/^/[\"/;s/$/\"]/')
NTP_CONFIG_JSON="[{\"Key\": \"ntp-servers\", \"Values\": "$NTP_IPS" }]"

# Get old DHCP Options configuration as JSON
DHCP_OPTIONS_ID=$(${awscmd} ec2 describe-vpcs --vpc-id=$VPC_ID --query 'Vpcs[0].DhcpOptionsId')
DHCP_OPTIONS_CONFIGS=$(${awscmd} ec2 describe-dhcp-options --dhcp-options-id=$DHCP_OPTIONS_ID --query 'DhcpOptions[0].DhcpConfigurations' --output=json)

# Remove old NTP servers section and add the new one
UPDATED_DHCP_CONFIGS=$(echo $DHCP_OPTIONS_CONFIGS | jq 'del(. [] | select(.Key == "ntp-servers"))')
UPDATED_DHCP_CONFIGS=$(jq ". + $NTP_CONFIG_JSON" <<<"$UPDATED_DHCP_CONFIGS")

# Reformat JSON as required by AWS CLI
DHCP_CONFIG_PAYLOAD=$(eval echo '{ \"DhcpConfigurations\": ${UPDATED_DHCP_CONFIGS} }')
DHCP_CONFIG_PAYLOAD=$(echo $DHCP_CONFIG_PAYLOAD | perl -pe 's/\{ \"Value\": (\".*?\") \}/$1/g')

# Create the DHCP Options set
UPDATED_DHCP_OPTIONS_ID=$(${awscmd} ec2 create-dhcp-options --cli-input-json "$DHCP_CONFIG_PAYLOAD" --query 'DhcpOptions.DhcpOptionsId')

# Associate the new DHCP Options set with the VPC
${awscmd} ec2 associate-dhcp-options --dhcp-options-id=$UPDATED_DHCP_OPTIONS_ID --vpc-id=$VPC_ID 

# Delete the old DHCP Options set
${awscmd} ec2 delete-dhcp-options --dhcp-options-id=$DHCP_OPTIONS_ID
