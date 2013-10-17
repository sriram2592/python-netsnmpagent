#!/usr/bin/env python
#
# python-netsnmpagent example agent with threading
#
# Copyright (c) 2013 Pieter Hollants <pieter@hollants.com>
# Licensed under the GNU Public License (GPL) version 3
#

#
# simple_agent.py demonstrates registering the various SNMP object types quite
# nicely but uses an inferior control flow logic: the main loop blocks in
# net-snmp's check_and_process() call until some event happens (eg. SNMP
# requests need processing). Only then will data be updated, not inbetween. And
# on the other hand, SNMP requests can not be handled while data is being
# updated, which might take longer periods of time.
#
# This example agent uses a more real life-suitable approach by outsourcing the
# data update process into a separate thread that gets woken up through an
# SIGALRM handler at an configurable interval. This does only ensure periodic
# data updates, it also makes sure that SNMP requests will always be replied to
# in time.
#
# Note that this implementation does not address possible locking issues: if
# a SNMP client's requests are processed while the data update thread is in the
# midst of refreshing the SNMP objects, the client might receive partially
# inconsistent data. 
#
# Use the included script run_threading_agent.sh to test this example.
#
# Alternatively, see the comment block in the head of simple_agent.py for
# adaptable instructions how to run this example against a system-wide snmpd
# instance.
#

import sys, os, signal, time
import optparse, threading, subprocess

# Make sure we use the local copy, not a system-wide one
sys.path.insert(0, os.path.dirname(os.getcwd()))
import netsnmpagent

prgname = sys.argv[0]

# Process command line arguments
parser = optparse.OptionParser()
parser.add_option(
	"-i",
	"--interval",
	dest="interval",
	help="Set interval in seconds between data updates",
	default=30
)
parser.add_option(
	"-m",
	"--mastersocket",
	dest="mastersocket",
	help="Sets the transport specification for the master agent's AgentX socket",
	default="/var/run/agentx/master"
)
parser.add_option(
	"-p",
	"--persistencedir",
	dest="persistencedir",
	help="Sets the path to the persistence directory",
	default="/var/lib/net-snmp"
)
(options, args) = parser.parse_args()

# Create an instance of the netsnmpAgent class
agent = netsnmpagent.netsnmpAgent(
	AgentName      = "ThreadingAgent",
	MasterSocket   = options.mastersocket,
	PersistenceDir = options.persistencedir,
	MIBFiles       = [ os.path.abspath(os.path.dirname(sys.argv[0])) +
	                   "/THREADING-MIB.txt" ]
)

# Register the only SNMP object we server, a DisplayString
threadingString = agent.DisplayString(
	oidstr  = "THREADING-MIB::threadingString",
	initval = "<No data available yet>"
)

headerlogged = 0
def LogMsg(msg):
	""" Writes a formatted log message with a timestamp to stdout. """

	global headerlogged

	if headerlogged == 0:
		print "{0:<8} {1:<90} {2}".format(
			"Time",
			"MainThread",
			"UpdateSNMPObjsThread"
		)
		print "{:-^120}".format("-")
		headerlogged = 1

	threadname = threading.currentThread().name

	funcname = sys._getframe(1).f_code.co_name
	funcname = "Main code path" if funcname == "<module>" \
	                            else "{0}()".format(funcname)

	if threadname == "MainThread":
		logmsg = "{0} {1:<112.112}".format(
			time.strftime("%T", time.localtime(time.time())),
			"{0}: {1}".format(funcname, msg)
		)
	else:
		logmsg = "{0} {1:>112.112}".format(
			time.strftime("%T", time.localtime(time.time())),
			"{0}: {1}".format(funcname, msg)
		)
	print logmsg

def UpdateSNMPObjs():
	""" Function that does the actual data update. """

	global threadingString

	LogMsg("Beginning data update.")
	data = ""

	# Obtain the data by calling an external command. We don't use
	# subprocess.check_output() here for compatibility with Python versions
	# older than 2.7.
	LogMsg("Calling external command \"sleep 5; date\".")
	proc = subprocess.Popen(
		"sleep 5; date", shell=True, env={ "LANG": "C" },
		stdout=subprocess.PIPE, stderr=subprocess.STDOUT
	)
	output = proc.communicate()[0].splitlines()[0]
	rc = proc.poll()
	if rc != 0:
		LogMsg("An error occured executing the command: {0}".format(output))
		return

	msg = "Updating \"threadingString\" object with data \"{0}\"."
	LogMsg(msg.format(output))
	threadingString.update(output)

	LogMsg("Data update done, exiting thread.")

def UpdateSNMPObjsAsync():
	""" Starts UpdateSNMPObjs() in a separate thread. """

	# UpdateSNMPObjs() will be executed in a separate thread so that the main
	# thread can continue looping and processing SNMP requests while the data
	# update is still in progress. However we'll make sure only one update
	# thread is run at any time, even if the data update interval has been set
	# too low.
	if threading.active_count() == 1:
		LogMsg("Creating thread for UpdateSNMPObjs().")
		t = threading.Thread(target=UpdateSNMPObjs, name="UpdateSNMPObjsThread")
		t.daemon = True
		t.start()
	else:
		LogMsg("Data update still active, data update interval too low?")

# Start the agent (eg. connect to the master agent).
agent.start()

# Trigger initial data update.
LogMsg("Doing initial call to UpdateSNMPObjsAsync().")
UpdateSNMPObjsAsync()

# Install a signal handler that terminates our threading agent when CTRL-C is
# pressed or a KILL signal is received
def TermHandler(signum, frame):
	global loop
	loop = False
signal.signal(signal.SIGINT, TermHandler)
signal.signal(signal.SIGTERM, TermHandler)

# Define a signal handler that takes care of updating the data periodically
def AlarmHandler(signum, frame):
	global loop, timer_triggered

	LogMsg("Got triggered by SIGALRM.")

	if loop:
		timer_triggered = True

		UpdateSNMPObjsAsync()

		signal.signal(signal.SIGALRM, AlarmHandler)
		signal.setitimer(signal.ITIMER_REAL, float(options.interval))
msg = "Installing SIGALRM handler triggered every {0} seconds."
msg = msg.format(options.interval)
LogMsg(msg)
signal.signal(signal.SIGALRM, AlarmHandler)
signal.setitimer(signal.ITIMER_REAL, float(options.interval))

# The threading agent's main loop. We loop endlessly until our signal
# handler above changes the "loop" variable.
LogMsg("Now serving SNMP requests, press ^C to terminate.")

loop = True
while loop:
	# Block until something happened (signal arrived, SNMP packets processed)
	timer_triggered = False
	res = agent.check_and_process()
	if res == -1 and not timer_triggered and loop:
		loop = False
		LogMsg("Error {0} in SNMP packet processing!".format(res))
	elif loop and timer_triggered:
		LogMsg("net-snmp's check_and_process() returned due to SIGALRM (res={0}), doing another loop.".format(res))
	elif loop:
		LogMsg("net-snmp's check_and_process() returned (res={0}), doing another loop.".format(res))

LogMsg("Terminating.")
