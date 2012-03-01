#! /usr/bin/env python

import argparse, getopt, sys, os, socket, time, ConfigParser
import futuregrid.virtual.cluster.cloudInstances

class cluster (object):
    
    def __init__(self):
        super(cluster, self).__init__()
        self.debug = False
        self.cloud_instances = cloudInstances.CloudInstances()


# ---------------------------------------------------------------------
# METHODS TO PRINT HELP MESSAGES
# ---------------------------------------------------------------------        

    def msg(self, message):
        print message


# ---------------------------------------------------------------------
# METHOD TO DETECT OPEN PORT
# ---------------------------------------------------------------------
    def parse_conf(self, file_name):
        
#        [virtual-cluster]
#        backup = directory/virtual-cluster.dat
#        userkey = directory/userkey.pem
#        ec2_cert = directory/cert.pem
#        ec2_private_key = directory/pk.pem
#        eucalyptus_cert = directory/cacert.pem
#        novarc = directory/novarc
        
        
        if file_name == None:
            file_name = 'dummy'
            
        config = ConfigParser.ConfigParser()
        
        # default location ~/.ssh/futuregrid.cfg
        config.read([os.path.expanduser('~/.ssh/futuregrid.cfg'), file_name])
        self.backup_file = config.get('virtual-cluster', 'backup')
        self.userkey = config.get('virtual-cluster', 'userkey')
        self.user = config.get('virtual-cluster', 'user')
        self.ec2_cert = config.get('virtual-cluster', 'ec2_cert')
        self.ec2_private_key = config.get('virtual-cluster', 'ec2_private_key')
        self.eucalyptus_cert = config.get('virtual-cluster', 'eucalyptus_cert')
        self.novarc = config.get('virtual-cluster', 'novarc')
        self.slurm = config.get('virtual-cluster', 'slurm')
        
        self.cloud_instances.set_backup_file(self.backup_file)

# ---------------------------------------------------------------------
# METHOD TO DETECT OPEN PORT
# ---------------------------------------------------------------------
    def detect_port(self):
        ready = 0
        
        # check if shh port of all VMs are alive
        while 1:   
            for instace in self.cloud_instances.get_list()[1:]:
                try:
                    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sk.settimeout(1)
                    sk.connect((instace['ip'], 22))
                    sk.close()
                    ready = ready + 1                    
                except Exception:
                    self.msg('Waiting VMs ready to deploy...')
                    ready = 0
                    time.sleep(1)     
            # check if all vms are ready            
            if ready == len(self.cloud_instances.get_list()[1:]):
                break

# ---------------------------------------------------------------------
# METHODS TO DO RPCs
# ---------------------------------------------------------------------
    def get_command_result(self, command):
        return os.popen(command).read() 
    
    def ssh (self, userkey, ip, command):
        os.system("ssh -i %s ubuntu@%s '%s'" % (userkey, ip, command))
        
    def scp (self, userkey, fileName, ip):
        os.system("scp -i %s %s ubuntu@%s:~/" % (userkey, fileName, ip)) 

# ---------------------------------------------------------------------
# METHODS TO CREATE A VIRTUAL CLUSTER
# ---------------------------------------------------------------------
    def euca_run_instance (self, userkey, cluster_size, image, instance_type):
        eui_overhead = 3
        eui_id_pos = 2
        # value changes depending on different version of euca2ools
        eui_len = 8

        instances = [x for x in self.get_command_result("euca-run-instances -k %s -n %d -t %s %s" % (userkey, cluster_size, instance_type, image)).split()]
        # parse command result store instances into cloud_instances list 
        for num in range(cluster_size):
            try:
                self.cloud_instances.set_instance(instances[num * eui_len + eui_id_pos + eui_overhead], image)
            except:
                self.msg('\nError in creating instances. Program will exit')
                sys.exit()

    def euca_associate_address (self, instance_id, ip):
        os.system("euca-associate-address -i %s %s" % (instance_id, ip))
        self.cloud_instances.set_ip_by_id(instance_id, ip)

    def euca_describe_addresses (self):
        ip_list = []
        ips = [x for x in os.popen("euca-describe-addresses").read().split('\n')]
        for ip in ips:
            if  ip.find('i-') < 0 and len(ip) > 0:
                ip_list.append(ip.split('\t')[1])
        return ip_list
            

    def create_cluster(self, args):
        
        self.parse_conf(args.file)
        if self.cloud_instances.if_exist(args.name):
            self.msg('\nError in creating virtual cluster %s, name is in use' %args.name)
            sys.exit() 
        
        self.msg('\n...Creating virtual cluster......')
        self.msg('cluster name    -- %s' %args.name)
        self.msg('numbe of nodes  -- %s' %args.number)
        self.msg('instance type   -- %s' %args.type)
        self.msg('image id        -- %s' %args.image)       
        
        
        self.cloud_instances.set_cloud_instances_by_name(args.name)
        
        cluster_size = int(args.number) + 1
        self.euca_run_instance (self.user, cluster_size, args.image, args.type)
        ip_lists = self.euca_describe_addresses ()

        # immediatly associate ip after run instance may lead to error, use sleep
        time.sleep(3)

        self.msg('\n...Associating IPs......')
        for i in range(cluster_size):
            instance = self.cloud_instances.get_by_id(i + 1)
            time.sleep(1)
            self.euca_associate_address (instance['id'], ip_lists[i])
            

        self.cloud_instances.save_instances()
        self.detect_port()
        self.deploy_services()

    def deploy_services(self):
        self.msg('\n...Deploying SLURM system......')

        for instance in self.cloud_instances.get_list()[1:]:
            self.ssh(self.userkey, instance['ip'], "sudo apt-get update")
            self.ssh(self.userkey, instance['ip'], "sudo apt-get install --yes slurm-llnl")
#            self.ssh(self.userkey, instance['ip'], "sudo apt-get install --yes openmpi-bin openmpi-doc libopenmpi-dev")

        self.msg('\n...Configuring slurm.conf......')
        with open(os.path.expanduser(self.slurm)) as srcf:
                input_content = srcf.readlines()
        srcf.close()
        
        controlMachine = self.cloud_instances.get_by_id(1)['id']
        output = "".join(input_content) % vars()

        destf = open("slurm.conf", "w")
        print >> destf, output
        destf.close()

        with open("slurm.conf", "a") as conf:
            for instance in self.cloud_instances.get_list()[2:]:
                conf.write("NodeName=%s Procs=1 State=UNKNOWN\n" % instance['id'])
                conf.write("PartitionName=debug Nodes=%s Default=YES MaxTime=INFINITE State=UP\n" 
                       % instance['id'])
        conf.close()

        self.msg('\n...generate munge-key......')
        # generate munge-key on control node
        self.ssh(self.userkey, self.cloud_instances.get_by_id(1)['ip'], "sudo /usr/sbin/create-munge-key")
        munge_key = open("munge.key", "w")
        print >> munge_key, self.get_command_result("ssh -i %s ubuntu@%s 'sudo cat /etc/munge/munge.key'" 
                               % (self.userkey, self.cloud_instances.get_by_id(1)['ip']))
        munge_key.close()

        for instance in self.cloud_instances.get_list()[1:]:
            # copy slurm.conf
            self.msg('\n...copying slurm.conf to node......')
            self.scp(self.userkey, "slurm.conf", instance['ip'])
            self.ssh(self.userkey, instance['ip'], "sudo cp slurm.conf /etc/slurm-llnl")

            # copy munge key
            self.msg('\n...copying munge-key to nodes......')
            self.scp(self.userkey, "munge.key", instance['ip'])
            self.ssh(self.userkey, instance['ip'], "sudo cp munge.key /etc/munge/munge.key")
            self.ssh(self.userkey, instance['ip'], "sudo chown munge /etc/munge/munge.key")
            self.ssh(self.userkey, instance['ip'], "sudo chgrp munge /etc/munge/munge.key")
            self.ssh(self.userkey, instance['ip'], "sudo chmod 400 /etc/munge/munge.key")
            
            # start slurm
            self.msg('\n...starting slurm......')
            self.ssh(self.userkey, instance['ip'], "sudo /etc/init.d/slurm-llnl start")
            self.ssh(self.userkey, instance['ip'], "sudo /etc/init.d/munge start")

# ---------------------------------------------------------------------
# METHODS TO SAVE RUNNING VIRTUAL CLUSTER
# ---------------------------------------------------------------------  
    def save_instance(self, kernel_id, ramdisk_id, ip, instance_name):
        if kernel_id == None:
            return os.popen("ssh -i %s ubuntu@%s '. ~/.profile; sudo euca-bundle-vol -c ${EC2_CERT} -k ${EC2_PRIVATE_KEY} -u ${EC2_USER_ID} --ec2cert ${EUCALYPTUS_CERT} --no-inherit -p %s -s 1024 -d /mnt/'" % (self.userkey, ip, instance_name)).read()
        elif ramdisk_id == None:
            return os.popen("ssh -i %s ubuntu@%s '. ~/.profile; sudo euca-bundle-vol -c ${EC2_CERT} -k ${EC2_PRIVATE_KEY} -u ${EC2_USER_ID} --ec2cert ${EUCALYPTUS_CERT} --no-inherit -p %s -s 1024 -d /mnt/ --kernel %s'" % (self.userkey, ip, instance_name, kernel_id)).read()
        else:
            return os.popen("ssh -i %s ubuntu@%s '. ~/.profile; sudo euca-bundle-vol -c ${EC2_CERT} -k ${EC2_PRIVATE_KEY} -u ${EC2_USER_ID} --ec2cert ${EUCALYPTUS_CERT} --no-inherit -p %s -s 1024 -d /mnt/ --kernel %s --ramdisk %s'" % (self.userkey, ip, instance_name, kernel_id, ramdisk_id)).read()

    def upload_bundle(self, ip, bucket_name, manifest):
        return os.popen("ssh -i %s ubuntu@%s '. ~/.profile; euca-upload-bundle -b %s -m %s'" % (self.userkey, ip, bucket_name, manifest)).read()

    def describe_images(self, image_id):
        return os.popen("euca-describe-images %s" % image_id).read()

    def get_kernel_id(self, image_id):
        command_result = [x for x in self.describe_images(image_id).split()]
        if len(command_result) >= 8:
            return command_result[7]

    def get_ramdisk_id(self, image_id):
        command_result = [x for x in self.describe_images(image_id).split()]
        if len(command_result) == 9:
            return command_result[8]
        
    def save_node(self, image_id, ip, bucket_name, image_name):
        kernel_id = self.get_kernel_id(image_id)
        ramdisk_id = self.get_ramdisk_id(image_id)
        manifest = [x for x in self.save_instance(kernel_id, ramdisk_id, ip, image_name).split()].pop()

        self.msg('\nmanifest generated: %s' % manifest)
        self.msg('\n...uploading bundle......')

        image = [x for x in self.upload_bundle(ip, bucket_name, manifest).split()].pop()

        self.msg('\n...registering image......')
        self.euca_register(image)

        
    def euca_register(self, image):
        os.system("euca-register %s" % image)

    def checkpoint_cluster(self, args):
        
        self.msg('\n...Saving virtual cluster......')
        self.msg('Virtual cluster name -- %s' %args.name)
        self.msg('control node bucket  -- %s' %args.controlb)
        self.msg('control node name    -- %s' %args.controln)
        self.msg('compute node bucket  -- %s' %args.computeb)
        self.msg('compute node name    -- %s' %args.computen)

        self.parse_conf(args.file)
        if not self.cloud_instances.if_exist(args.name):
            self.msg('Error in locating virtual cluster %s, not created' %args.name)
            sys.exit()
        self.cloud_instances.get_cloud_instances_by_name(args.name)
        
        for instance in self.cloud_instances.get_list()[1:3]:
            self.scp(self.userkey, self.ec2_cert, instance['ip'])
            self.scp(self.userkey, self.ec2_private_key, instance['ip'])
            self.scp(self.userkey, self.eucalyptus_cert, instance['ip'])
            self.scp(self.userkey, self.novarc, instance['ip'])
            self.ssh(self.userkey, instance['ip'], "cat novarc >> ~/.profile")
            self.ssh(self.userkey, instance['ip'], "source ~/.profile")

        #save control node
        self.save_node(self.cloud_instances.get_by_id(1)['image'],
                   self.cloud_instances.get_by_id(1)['ip'],
                   args.controlb,
                   args.controln)
        
        #save compute node
        self.save_node(self.cloud_instances.get_by_id(2)['image'],
                   self.cloud_instances.get_by_id(2)['ip'],
                   args.computeb,
                   args.computen)

# ---------------------------------------------------------------------
# METHODS TO RESTORE VIRTUAL CLUSTER
# ---------------------------------------------------------------------  
    def restore_cluster(self, args):

        self.parse_conf(args.file)
        if self.cloud_instances.if_exist(args.name):
            self.msg('Error in creating virtual cluster %s, name is in use' %args.name)
            sys.exit() 

        self.cloud_instances.set_cloud_instances_by_name(args.name)
        cluster_size = int(args.number) + 1
        self.msg('\n...Restoring virtual cluster......')
        self.msg('cluster name      -- %s' %args.name)
        self.msg('number of nodes   -- %s' %cluster_size)
        self.msg('instance type     -- %s' %args.type)
        self.msg('control image     -- %s' %args.controli)
        self.msg('compute image     -- %s' %args.computei)

        self.euca_run_instance(self.user, 1, args.controli, args.type)
        self.euca_run_instance(self.user, int(args.number), args.computei, args.type)    

        ip_lists = self.euca_describe_addresses ()

        # immediatly associate ip after run instance may lead to error, use sleep
        time.sleep(3)

        self.msg('...Associating IPs......')
        for i in range(cluster_size):
            time.sleep(1)
            instance = self.cloud_instances.get_by_id(i+1)
            self.euca_associate_address (instance['id'], ip_lists[i])

        self.cloud_instances.save_instances()
    
        with open(os.path.expanduser(self.slurm)) as srcf:
                input_content = srcf.readlines()
        srcf.close()
        
        controlMachine = self.cloud_instances.get_by_id(1)['id']
        output = "".join(input_content) % vars()

        destf = open("slurm.conf", "w")
        print >> destf, output
        destf.close()

        with open("slurm.conf", "a") as conf:
            for instance in self.cloud_instances.get_list()[2:]:
                conf.write("NodeName=%s Procs=1 State=UNKNOWN\n" % instance['id'])
                conf.write("PartitionName=debug Nodes=%s Default=YES MaxTime=INFINITE State=UP\n" 
                       % instance['id'])
        conf.close()

        self.detect_port()

        self.msg('\n...Configuring SLURM......')
        for instance in self.cloud_instances.get_list()[1:]:
            # copy slurm.conf
            print '\n...copying slurm.conf to node......'
            self.scp(self.userkey, "slurm.conf", instance['ip'])
            self.ssh(self.userkey, instance['ip'], "sudo cp slurm.conf /etc/slurm-llnl")

            # start slurm
            print '\n...starting slurm......'
            self.ssh(self.userkey, instance['ip'], "sudo /etc/init.d/slurm-llnl start")
            self.ssh(self.userkey, instance['ip'], "sudo /etc/init.d/munge start")


# ---------------------------------------------------------------------
# METHODS TO TERMINATE NAD CLEANUP
# ---------------------------------------------------------------------        

    def clean(self, name):
        self.msg('\r Clearing up the instance: progress')
        self.cloud_instances.del_by_name(name)
        self.msg('\r Clearing up the instance: completed')
    
    def terminate_instance(self, instance_id):
        self.msg('terminating instance %s' % instance_id)
        os.system("euca-terminate-instances %s" % instance_id)

    def shut_down(self, args):
        self.parse_conf(args.file)
        if not self.cloud_instances.if_exist(args.name):
            self.msg('\nError in finding virtual cluster %s, not created.' %args.name)
            sys.exit() 
            
        self.cloud_instances.get_cloud_instances_by_name(args.name)
                
        for instance in self.cloud_instances.get_list()[1:]:
            self.terminate_instance(instance['id'])
        self.clean(args.name)

# ---------------------------------------------------------------------
# METHODS TO SHOW VIRTUAL CLUSTER(S) STATUS
# ---------------------------------------------------------------------   
    def show_status(self, args):
        
        self.parse_conf(args.file)
        
        if not args.name:
            for cloud in self.cloud_instances.get_all_cloud_instances():
                self.msg('\n=============================')
                self.msg('Virtual Cluster %s' %cloud[0]['name'])
                self.msg('=============================')
                for instance in cloud[1:]:
                    self.msg("instance %s: IP -- %s, image -- %s" %(instance['id'], instance['ip'], instance['image'])) 
        else:  
            if not self.cloud_instances.if_exist(args.name):
                self.msg('Error in finding virtual cluster %s, not created.' %args.name)
                sys.exit() 
            self.cloud_instances.get_cloud_instances_by_name(args.name)
            self.msg('=============================')
            self.msg('Virtual Cluster %s' %args.name)
            self.msg('=============================')
            for instance in self.cloud_instances.get_list()[1:]:
                self.msg("instance %s: IP -- %s, image -- %s" %(instance['id'], instance['ip'], instance['image'])) 
            

######################################################################
# MAIN
######################################################################

def commandline_parser():
    
    virtual_cluster = cluster()
    
    parser = argparse.ArgumentParser(description='Virtual cluster management operations')
    parser.add_argument('-f', '--file', action='store', help ='Specify futuregrid configure file')
    parser.add_argument('--debug', action='store_true')
    subparsers = parser.add_subparsers(help='commands')

    # status command
    status_parser = subparsers.add_parser('status', help='Show virtual cluster status')
    status_parser.add_argument('-a', '--name', action='store', help='Show status of virtual cluster of given name')
    status_parser.set_defaults(func=virtual_cluster.show_status)
    
    # run command
    run_parser = subparsers.add_parser('run', help='Create a virtual cluster')
    run_parser.add_argument('-a', '--name', action='store', required = True, help='Virtual cluster name')
    run_parser.add_argument('-n', '--number', action='store', required = True, help='Numbe of compute nodes')
    run_parser.add_argument('-t', '--type', action='store', required = True, help='Instance type')
    run_parser.add_argument('-i', '--image', action='store', required = True, help='Image id')
    run_parser.set_defaults(func=virtual_cluster.create_cluster)
    
    # terminate command
    terminate_parser = subparsers.add_parser('terminate', help='Terminate virtual cluster')
    terminate_parser.add_argument('-a', '--name', action='store', required = True, help='Virtual cluster name')
    terminate_parser.set_defaults(func=virtual_cluster.shut_down)
    
    # checkpoint command
    checkpoint_parser = subparsers.add_parser('checkpoint', help='Save virtual cluster')
    checkpoint_parser.add_argument('-a', '--name', action='store', required = True, help='Virtual cluster name')
    checkpoint_parser.add_argument('-c', '--controlb', action='store', required = True, help='Control node bucket name')
    checkpoint_parser.add_argument('-t', '--controln', action='store', required = True, help='Control node image name')
    checkpoint_parser.add_argument('-m', '--computeb', action='store', required = True, help='Compute node bucket name')
    checkpoint_parser.add_argument('-e', '--computen', action='store', required = True, help='Compute node image name')
    checkpoint_parser.set_defaults(func=virtual_cluster.checkpoint_cluster)
    
    # restore command
    restore_parser = subparsers.add_parser('restore', help='Restore saved virtual cluster')
    restore_parser.add_argument('-a', '--name', action='store', required = True, help='Virtual cluster name')
    restore_parser.add_argument('-c', '--controli', action='store', required = True, help='Control node image id')
    restore_parser.add_argument('-m', '--computei', action='store', required = True, help='Compute node image id')
    restore_parser.add_argument('-t', '--type', action='store', help='Instance type')
    restore_parser.add_argument('-n', '--number', action='store', required = True, help='Number of compute nodes')
    restore_parser.set_defaults(func=virtual_cluster.restore_cluster)
    
    # list command
    list_parser = subparsers.add_parser('list', help='Return intances list?')
    
    args = parser.parse_args()
    args.func(args)

if __name__ == '__main__':
    commandline_parser()