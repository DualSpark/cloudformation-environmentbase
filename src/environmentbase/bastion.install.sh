#!/bin/bash
mkdir -p /etc/facter/facts.d
cat > /tmp/cloud_node_type.yml << EOF
---
sys_environment: $PUPPET_ENVIRONMENT
EOF
mv /tmp/cloud_node_type.yml /etc/facter/facts.d/cloud_node_type.yaml

cat > /tmp/puppet.conf << EOF
[main]
    logdir = /var/log/puppet
    rundir = /var/run/puppet
    ssldir = $vardir/ssl

[agent]
    server = $PUPPET_DNS_NAME
    classfile = $vardir/classes.txt
    localconfig = $vardir/localconfig
EOF

mv /tmp/puppet.conf /etc/puppet/puppet.conf

puppet agent -t --no-daemonize
puppet resource service puppet ensure=running enable=true
