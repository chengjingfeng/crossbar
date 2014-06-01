###############################################################################
##
##  Copyright (C) 2014 Tavendo GmbH
##
##  This program is free software: you can redistribute it and/or modify
##  it under the terms of the GNU Affero General Public License, version 3,
##  as published by the Free Software Foundation.
##
##  This program is distributed in the hope that it will be useful,
##  but WITHOUT ANY WARRANTY; without even the implied warranty of
##  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
##  GNU Affero General Public License for more details.
##
##  You should have received a copy of the GNU Affero General Public License
##  along with this program. If not, see <http://www.gnu.org/licenses/>.
##
###############################################################################

from __future__ import absolute_import

__all__ = ['Node']


import os
import sys
import json
import traceback

from twisted.python import log
from twisted.internet.defer import Deferred, \
                                   DeferredList, \
                                   returnValue, \
                                   inlineCallbacks

from autobahn import wamp
from autobahn.wamp.types import CallDetails
from autobahn.wamp.router import RouterFactory
from autobahn.twisted.wamp import RouterSessionFactory

from crossbar.common import checkconfig
from crossbar.controller.process import NodeControllerSession


from autobahn.wamp.types import ComponentConfig




class Node:
   """
   A Crossbar.io node is the running a controller process
   and one or multiple worker processes.

   A single Crossbar.io node runs exactly one instance of
   this class, hence this class can be considered a system
   singleton.
   """

   def __init__(self, reactor, options):
      """
      Ctor.

      :param reactor: Reactor to run on.
      :type reactor: obj
      :param options: Options from command line.
      :type options: obj
      """
      self.options = options
      ## the reactor under which we run
      self._reactor = reactor

      ## shortname for reactor to run (when given via explicit option) or None
      self._reactor_shortname = options.reactor

      ## node directory
      self._cbdir = options.cbdir

      ## the node's name (must be unique within the management realm)
      self._node_id = None

      ## the node's management realm
      self._realm = None

      ## node controller session (a singleton ApplicationSession embedded
      ## in the node's management router)
      self._controller = None



   def start(self):
      """
      Starts this node. This will start a node controller and then spawn new worker
      processes as needed.
      """
      ## for now, a node is always started from a local configuration
      ##
      configfile = os.path.join(self.options.cbdir, self.options.config)
      log.msg("Starting from local configuration '{}'".format(configfile))
      config = checkconfig.check_config_file(configfile, silence = True)

      self.start_from_config(config)



   def start_from_config(self, config):

      title = config['controller'].get('title', 'crossbar-controller')

      try:
         import setproctitle
      except ImportError:
         log.msg("Warning, could not set process title (setproctitle not installed)")
      else:
         setproctitle.setproctitle(title)


      ## the node's name (must be unique within the management realm)
      self._node_id = config['controller'].get('id', 'node1')

      ## the node's management realm
      self._realm = config['controller'].get('realm', 'crossbar')


      ## the node controller singleton WAMP application session
      ##
      #session_config = ComponentConfig(realm = options.realm, extra = options)

      self._controller = NodeControllerSession(self)

      ## router and factory that creates router sessions
      ##
      self._router_factory = RouterFactory(
         options = wamp.types.RouterOptions(uri_check = wamp.types.RouterOptions.URI_CHECK_LOOSE),
         debug = False)
      self._router_session_factory = RouterSessionFactory(self._router_factory)

      ## add the node controller singleton session to the router
      ##
      self._router_session_factory.add(self._controller)

      ## Detect WAMPlets
      ##
      wamplets = self._controller._get_wamplets()
      if len(wamplets) > 0:
         log.msg("Detected {} WAMPlets in environment:".format(len(wamplets)))
         for wpl in wamplets:
            log.msg("WAMPlet {}.{}".format(wpl['dist'], wpl['name']))
      else:
         log.msg("No WAMPlets detected in enviroment.")


      self.run_node_config(config)



   def _start_from_local_config(self, configfile):
      """
      Start Crossbar.io node from local configuration file.
      """
      configfile = os.path.abspath(configfile)
      log.msg("Starting from local config file '{}'".format(configfile))

      try:
         config = controller.config.check_config_file(configfile, silence = True)
         #config = json.loads(open(configfile, 'rb').read())
      except Exception as e:
         log.msg("Fatal: {}".format(e))
         sys.exit(1)
      else:
         self.run_node_config(config)



   @inlineCallbacks
   def run_node_config(self, config):
      try:
         yield self._run_node_config(config)
      except:
         traceback.print_exc()
         self._reactor.stop()



   @inlineCallbacks
   def _run_node_config(self, config):
      """
      Setup node according to config provided.
      """

      ## fake call details information when calling into
      ## remoted procedure locally
      ##
      call_details = CallDetails(caller = 0, authid = 'node')

      controller = config.get('controller', {})


      ## start Manhole in node controller
      ##
      if 'manhole' in controller:
         yield self._controller.start_manhole(controller['manhole'], details = call_details)


      ## start local transport for management router
      ##
      if 'transport' in controller:
         yield self._controller.start_management_transport(controller['transport'], details = call_details)


      ## startup all workers
      ##
      worker_no = 1

      for worker in config.get('workers', []):

         ## worker ID, type and logname
         ##
         if 'id' in worker:
            worker_id = worker.pop('id')
         else:
            worker_id = 'worker{}'.format(worker_no)
            worker_no += 1

         worker_type = worker['type']
         worker_options = worker.get('options', {})

         if worker_type == 'router':
            worker_logname = "Router '{}'".format(worker_id)

         elif worker_type == 'container':
            worker_logname = "Container '{}'".format(worker_id)

         elif worker_type == 'guest':
            worker_logname = "Guest '{}'".format(worker_id)

         else:
            raise Exception("logic error")


         ## router/container
         ##
         if worker_type in ['router', 'container']:

            ## start a new native worker process ..
            ##
            if worker_type == 'router':
               yield self._controller.start_router(worker_id, worker_options, details = call_details)

            elif worker_type == 'container':
               yield self._controller.start_container(worker_id, worker_options, details = call_details)

            else:
               raise Exception("logic error")


            ## setup native worker generic stuff
            ##
            if 'pythonpath' in worker_options:
               added_paths = yield self._controller.call('crossbar.node.{}.worker.{}.add_pythonpath'.format(self._node_id, worker_id), worker_options['pythonpath'])
               log.msg("{}: PYTHONPATH extended for {}".format(worker_logname, added_paths))

            if 'cpu_affinity' in worker_options:
               new_affinity = yield self._controller.call('crossbar.node.{}.worker.{}.set_cpu_affinity'.format(self._node_id, worker_id), worker_options['cpu_affinity'])
               log.msg("{}: CPU affinity set to {}".format(worker_logname, new_affinity))

            if 'manhole' in worker:
               yield self._controller.call('crossbar.node.{}.worker.{}.start_manhole'.format(self._node_id, worker_id), worker['manhole'])
               log.msg("{}: manhole started".format(worker_logname))


            ## setup router worker
            ##
            if worker_type == 'router':

               ## start realms on router
               ##
               realm_no = 1

               for realm in worker.get('realms', []):

                  if 'id' in realm:
                     realm_id = realm.pop('id')
                  else:
                     realm_id = 'realm{}'.format(realm_no)
                     realm_no += 1

                  ## FIXME
                  #yield self._controller.call('crossbar.node.{}.worker.{}.start_router_realm'.format(self._node_id, worker_id), realm_id, realm)
                  #log.msg("{}: realm '{}' started".format(worker_logname, realm_id))


               ## start components to run embedded in the router
               ##
               component_no = 1

               for component in worker.get('components', []):

                  if 'id' in component:
                     component_id = component.pop('id')
                  else:
                     component_id = 'component{}'.format(component_no)
                     component_no += 1

                  yield self._controller.call('crossbar.node.{}.worker.{}.start_router_component'.format(self._node_id, worker_id), component_id, component)
                  log.msg("{}: component '{}' started".format(worker_logname, component_id))


               ## start transports on router
               ##
               transport_no = 1

               for transport in worker['transports']:

                  if 'id' in transport:
                     transport_id = transport.pop('id')
                  else:
                     transport_id = 'transport{}'.format(transport_no)
                     transport_no += 1

                  yield self._controller.call('crossbar.node.{}.worker.{}.start_router_transport'.format(self._node_id, worker_id), transport_id, transport)
                  log.msg("{}: transport '{}' started".format(worker_logname, transport_id))


            ## setup container worker
            ##
            elif worker_type == 'container':

               component_no = 1

               for component in worker.get('components', []):

                  if 'id' in component:
                     component_id = component.pop('id')
                  else:
                     component_id = 'component{}'.format(component_no)
                     component_no += 1

                  yield self._controller.call('crossbar.node.{}.worker.{}.start_container_component'.format(self._node_id, worker_id), component_id, component)
                  log.msg("{}: component '{}' started".format(worker_logname, component_id))

            else:
               raise Exception("logic error")


         elif worker_type == 'guest':

            ## start guest worker
            ##
            yield self._controller.start_guest(worker_id, worker, details = call_details)
            log.msg("{}: started".format(worker_logname))

         else:
            raise Exception("logic error")
