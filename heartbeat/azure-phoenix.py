#!/usr/bin/python

import sys, re, os, subprocess, time, syslog
import logging
import azure_fence_lib

OCF_SUCCESS = 0
OCF_ERR_GENERIC = 1
OCF_ERR_UNIMPLEMENTED = 3
OCF_ERR_CONFIGURED = 6
OCF_NOT_RUNNING = 7

AZR_VM_FENCED = 1
AZR_VM_NOT_FENCED = 2

OCF_RESOURCE_INSTANCE = None
PROCESS_EXEC_NAME = None
PROCESS_EXEC_ARG = None
PID_FILE = "azure-phoenix-{}.pid"

## https://github.com/ClusterLabs/fence-agents/blob/master/fence/agents/lib/fencing.py.py
## Own logger handler that uses old-style syslog handler as otherwise everything is sourced
## from /dev/syslog
class SyslogLibHandler(logging.StreamHandler):
	"""
	A handler class that correctly push messages into syslog
	"""
	def emit(self, record):
		syslog_level = {
			logging.CRITICAL:syslog.LOG_CRIT,
			logging.ERROR:syslog.LOG_ERR,
			logging.WARNING:syslog.LOG_WARNING,
			logging.INFO:syslog.LOG_INFO,
			logging.DEBUG:syslog.LOG_DEBUG,
			logging.NOTSET:syslog.LOG_DEBUG,
		}[record.levelno]

		msg = self.format(record)

		# syslos.syslog can not have 0x00 character inside or exception is thrown
		syslog.syslog(syslog_level, msg.replace("\x00", "\n"))
		return

def get_azure_config():
    config = azure_fence_lib.AzureConfiguration()

    config.RGName = os.environ.get("OCF_RESKEY_resourceGroup")
    config.VMName = os.environ.get("OCF_RESKEY_vmName")
    config.SubscriptionId = os.environ.get("OCF_RESKEY_subscriptionId")
    config.Cloud = os.environ.get("OCF_RESKEY_cloud")
    config.UseMSI = os.environ.get("OCF_RESKEY_useMSI")
    config.Tenantid = os.environ.get("OCF_RESKEY_tenantId")
    config.ApplicationId = os.environ.get("OCF_RESKEY_applicationId")
    config.ApplicationKey = os.environ.get("OCF_RESKEY_applicationKey")
    config.Verbose = os.environ.get("OCF_RESKEY_verbose")
    
    if not config.RGName:
        logging.info("resourceGroup not provided. Using metadata service")
        config.RGName = azure_fence_lib.get_resource_group_from_metadata()

    if not config.SubscriptionId:
        logging.info("subscriptionId not provided. Using metadata service")
        config.SubscriptionId = azure_fence_lib.get_subscription_id_from_metadata()

    return config

def check_azure_config(config):
    
    if not config.RGName:
        fail_usage("Parameter resourceGroup required.")
    if not config.VMName:
        fail_usage("Parameter vmName required.")
    if not config.SubscriptionId:
        fail_usage("Parameter subscriptionId required.")

    if not azure_fence_lib.ocf_is_true(config.UseMSI):
        if not config.Tenantid:
            fail_usage("Parameter tenantId required if Service Principal should be used.")
        if not config.ApplicationId:
            fail_usage("Parameter applicationId required if Service Principal should be used.")
        if not config.ApplicationKey:
            fail_usage("Parameter applicationKey required if Service Principal should be used.")

    if config.Cloud and not (config.Cloud.lower() in ("china", "germany", "usgov")):
        fail_usage("Value %s for cloud parameter not supported. Supported values are china, germany and usgov" % config.Cloud)


# https://stackoverflow.com/questions/32295395/how-to-get-the-process-name-by-pid-in-linux-using-python
def get_pname(id):
    p = subprocess.Popen(["ps -o cmd= {}".format(id)], stdout=subprocess.PIPE, shell=True)
    return str(p.communicate()[0]).strip()

# https://stackoverflow.com/questions/568271/how-to-check-if-there-exists-a-process-with-a-given-pid-in-python
def check_pid(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True

def get_pid_file():
    return PID_FILE.format(OCF_RESOURCE_INSTANCE)

def print_help():
    print("""This resource agent is part of the fencing solution for Azure.
        It implements the on operation of the Azure fencing.
    
    Usage:
      azure-phoenix <action> resourceGroup=<val> vmName=<val> subscriptionId=<val>
        cloud=<val> useMSI=<val> tenantId=<val> applicationId=<val> applicationKey=<val>
    
      action (required): Supported values are: start, stop,
                         monitor, meta-data, validate-all
      resourceGroup (required): Name of the resource group
      vmName (required): Name of the virtual machine that this
                         resource agent instance should unfence
      subscriptionId (required): Id of the Azure subscription
      cloud (optional): Name of the cloud you want to use. Supported values are
                        china, germany or usgov. Do not use this parameter if
                        you want to use public Azure.
      useMSI (optional): Determines if Managed Service Identity should be used
                         instead of username and password (Service Principal).
                         If this parameter is specified, parameters
                         tenantId, applicationId and applicationKey are ignored.
      tenantId (optional): Id of the Azure Active Directory tenant.
                           Only required if a Service Principal should be used
      applicationId (optional): Application ID of the Service Principal.
                                Only required if a Service Principal should be used
      applicationKey (optional): Authentication key of the Service Principal.
                                 Only required if a Service Principal should be used""")

def print_metadata():
    print("""<?xml version="1.0"?>
    <!DOCTYPE resource-agent SYSTEM "ra-api-1.dtd">
    <resource-agent name="azure-phoenix">
      <version>0.1</version>
      <longdesc lang="en">
        This resource agent is part of the fencing solution for Azure. It implements the on operation of the Azure fencing.
      </longdesc>
      <shortdesc lang="en">Azure resource agent for fencing on</shortdesc>
      <parameters>

        <parameter name="resourceGroup" unique="0" required="0">
          <longdesc lang="en">
            Name of the resource group. Metadata service is used if the value is not provided.
          </longdesc>
          <shortdesc lang="en">Name of the resource group</shortdesc>
          <content type="string"/>
        </parameter>

        <parameter name="vmName" unique="0" required="1">
          <longdesc lang="en">
            Name of the virtual machine that this resource agent instance should unfence
          </longdesc>
          <shortdesc lang="en">Name of the virtual machine</shortdesc>
          <content type="string"/>
        </parameter>

        <parameter name="subscriptionId" unique="0" required="0">
          <longdesc lang="en">
            Id of the Azure subscription. Metadata service is used if the value is not provided.
          </longdesc>
          <shortdesc lang="en">Id of the Azure subscription</shortdesc>
          <content type="string"/>
        </parameter>

        <parameter name="cloud" unique="0" required="0">
          <longdesc lang="en">
            Name of the cloud you want to use. Supported values are china, germany or usgov. Do not use this parameter if you want to use public Azure.
          </longdesc>
          <shortdesc lang="en">Name of the cloud you want to use.</shortdesc>
          <content type="string"/>
        </parameter>

        <parameter name="useMSI" unique="0" required="0">
          <longdesc lang="en">
            Determines if Managed Service Identity should be used instead of username and password (Service Principal). If this parameter is specified, parameters tenantId, applicationId and applicationKey are ignored.
          </longdesc>
          <shortdesc lang="en">Determines if Managed Service Identity should be used.</shortdesc>
          <content type="boolean"/>
        </parameter>

        <parameter name="tenantId" unique="0" required="0">
          <longdesc lang="en">
            Id of the Azure Active Directory tenant. Only required if a Service Principal should be used
          </longdesc>
          <shortdesc lang="en">Id of the Azure Active Directory tenant</shortdesc>
          <content type="string"/>
        </parameter>

        <parameter name="applicationId" unique="0" required="0">
          <longdesc lang="en">
            Application ID of the Service Principal. Only required if a Service Principal should be used
          </longdesc>
          <shortdesc lang="en">Application Id</shortdesc>
          <content type="string"/>
        </parameter>

        <parameter name="applicationKey" unique="0" required="0">
          <longdesc lang="en">
            Authentication key of the Service Principal. Only required if a Service Principal should be used
          </longdesc>
          <shortdesc lang="en">Authentication key</shortdesc>
          <content type="string"/>
        </parameter>

        <parameter name="verbose" unique="0" required="0">
          <longdesc lang="en">
            Enables verbose output
          </longdesc>
          <shortdesc lang="en">Enables verbose output</shortdesc>
          <content type="boolean"/>
        </parameter>

      </parameters>
      <actions>
        <action name="start"        timeout="900" />
        <action name="stop"         timeout="20" />
        <action name="monitor"      timeout="20" interval="10" depth="0" />
        <action name="meta-data"    timeout="5" />
      </actions>
    </resource-agent>""")

    return OCF_SUCCESS

def fail_usage(message):
    logging.error("%s Run with parameter help to get more information" % message)
    sys.exit(OCF_ERR_CONFIGURED)

def action_start(config):

    if (action_monitor() == OCF_SUCCESS):
        logging.info("action_start: Resource is already running")
        return OCF_SUCCESS
    
    if os.path.exists(get_pid_file()):
        os.remove(get_pid_file())

    file = open(get_pid_file(), "w")
    file.close()

    logging.info("action_start: Starting new process. Using pid file %s" % get_pid_file())
    p = subprocess.Popen(PROCESS_EXEC_ARG, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    logging.info("action_start: process started. pid %s" % p.pid)   
    file = open(get_pid_file(), "w") 
    file.write(str(p.pid))
    file.close()

    if get_fence_status(config) == AZR_VM_FENCED:
        return set_power_status(config)
    
    return OCF_SUCCESS

def action_stop():

    logging.info("action_stop: Testing if resource is running")
    if (action_monitor() == OCF_NOT_RUNNING):
        logging.info("action_stop: Resource is not running")
        return OCF_SUCCESS
    
    logging.info("action_stop: Resource is running. Removing pid file")
    if os.path.exists(get_pid_file()):
        os.remove(get_pid_file())
    else:
        logging.error("action_stop: pid file does not exist")
        return OCF_ERR_GENERIC

    logging.info("action_stop: pid file removed. Testing status")
    if (action_monitor() == OCF_NOT_RUNNING):
        logging.info("action_stop: Resource is not running")
        return OCF_SUCCESS
    else:
        logging.error("action_stop: stop failed. Resource still running")
        return OCF_ERR_GENERIC

def action_monitor():

    if os.path.exists(get_pid_file()):
        logging.info("action_monitor: pid file exist")
        file = open(get_pid_file(), "r") 
        strpid = file.read()
        file.close()

        if strpid and check_pid(int(strpid)):
            logging.info("action_monitor: process with pid %s is running" % strpid)
            name = get_pname(strpid)
            logging.info("action_monitor: process with pid %s has name '%s'" % (strpid, name))

            if (name == PROCESS_EXEC_NAME):
                return OCF_SUCCESS
            else:
                logging.info("action_monitor: no process with name '%s'" % PROCESS_EXEC_NAME)
                return OCF_NOT_RUNNING
        else:
            logging.info("action_monitor: no process for pid %s" % strpid)
            return OCF_NOT_RUNNING
    else:
        logging.info("action_monitor: pid file does not exist")
        return OCF_NOT_RUNNING

def get_fence_status(config):
    logging.info("get_fence_status: getting fence status for virtual machine %s" % config.VMName)
    result = AZR_VM_NOT_FENCED
        
    try:
        compute_client = azure_fence_lib.get_azure_compute_client(config)
        network_client = azure_fence_lib.get_azure_network_client(config)

        if azure_fence_lib.get_power_status_impl(compute_client, network_client, config.RGName, config.VMName) == azure_fence_lib.FENCE_STATE_OFF:
            result = AZR_VM_FENCED

    except Exception as e:
        fail_usage("get_fence_status: Failed: %s" % e)
    
    logging.info("get_fence_status: result is %s (AZR_VM_FENCED: %s, AZR_VM_NOT_FENCED: %s)" % (result, AZR_VM_FENCED, AZR_VM_NOT_FENCED))
    return result

def set_power_status(config):    
    logging.info("set_power_status: unfencing virtual machine %s in resource group %s" % (config.VMName, config.RGName))
    
    try:
        compute_client = azure_fence_lib.get_azure_compute_client(config)
        network_client = azure_fence_lib.get_azure_network_client(config)

        azure_fence_lib.set_power_status_on(compute_client, network_client, config.RGName, config.VMName)

    except ImportError as ie:
        logging.error("set_power_status: Azure Resource Manager Python SDK not found or not accessible: %s" % re.sub("^, ", "", str(ie)))
        return OCF_ERR_GENERIC
    except Exception as e:
        logging.error("set_power_status: Failed: %s" % re.sub("^, ", "", str(e)))
        return OCF_ERR_GENERIC

    return OCF_SUCCESS

def action_validate_all(config):
    logging.info("action_validate_all: start")

    try:
        compute_client = azure_fence_lib.get_azure_compute_client(config)
        vm = compute_client.virtual_machines.get(config.RGName, config.VMName, "instanceView")
    except Exception as e:
        fail_usage("action_validate_all: Failed: %s" % e)
    
    return OCF_SUCCESS

def main():
    config = get_azure_config()

    if (azure_fence_lib.ocf_is_true(config.Verbose)):
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.WARNING)
    
    logging.getLogger().addHandler(SyslogLibHandler())	
    logging.getLogger().addHandler(logging.StreamHandler(sys.stderr))

    global OCF_RESOURCE_INSTANCE
    global PROCESS_EXEC_NAME
    global PROCESS_EXEC_ARG

    OCF_RESOURCE_INSTANCE = os.environ.get("OCF_RESOURCE_INSTANCE")
    if not OCF_RESOURCE_INSTANCE:
        OCF_RESOURCE_INSTANCE = "unknown"
    
    PROCESS_EXEC_NAME = 'tailf {}'.format(get_pid_file())
    PROCESS_EXEC_ARG = ['tailf', get_pid_file()]

    action = None
    if len(sys.argv) > 1:
            action = sys.argv[1]    

    
    logging.debug("main: action is %s" % action)

    result = OCF_ERR_UNIMPLEMENTED
    if action == "meta-data":
        result = print_metadata()
    elif action == "help":
        print_help()
    else:
        check_azure_config(config)

        if action == "monitor":
            result = action_monitor()
        elif action == "stop":
            result = action_stop()
        elif action == "start":
            result = action_start(config)
        elif action == "validate-all":
            result = action_validate_all(config)
        elif action:
            result = OCF_ERR_UNIMPLEMENTED 

    logging.debug("main: Done %s" % result)
    sys.exit(result)

if __name__ == "__main__":
    main()
