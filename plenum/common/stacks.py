import time
import zmq

from random import randint
from typing import Callable, Any, List, Dict

from plenum.common.batched import Batched, logger
from plenum.common.config_util import getConfig, \
    get_global_config_else_read_config
from plenum.common.message_processor import MessageProcessor
from plenum.recorder.simple_zstack_with_recorder import SimpleZStackWithRecorder
from plenum.recorder.simple_zstack_with_silencer import SimpleZStackWithSilencer
from stp_core.common.constants import CONNECTION_PREFIX
from stp_core.types import HA
from stp_zmq.kit_zstack import KITZStack
from stp_zmq.simple_zstack import SimpleZStack

# conf_ = getConfigOnce()
conf_ = get_global_config_else_read_config()

if conf_.STACK_COMPANION == 1:
    simple_zstack_class = SimpleZStackWithRecorder
elif conf_.STACK_COMPANION == 2:
    simple_zstack_class = SimpleZStackWithSilencer
else:
    simple_zstack_class = SimpleZStack


class ClientZStack(simple_zstack_class, MessageProcessor):
    def __init__(self, stackParams: dict, msgHandler: Callable, seed=None,
                 config=None, msgRejectHandler=None):
        config = config or getConfig()
        simple_zstack_class.__init__(
            self,
            stackParams,
            msgHandler,
            seed=seed,
            onlyListener=True,
            config=config,
            msgRejectHandler=msgRejectHandler,
            create_listener_monitor=True)
        MessageProcessor.__init__(self, allowDictOnly=False)

        self.max_connected_clients_num = config.MAX_CONNECTED_CLIENTS_NUM
        self.postrestart_wait_time = config.STACK_POSTRESTART_WAIT_TIME
        self.min_stack_restart_timeout = config.MIN_STACK_RESTART_TIMEOUT
        self.max_stack_restart_time_deviation = config.MAX_STACK_RESTART_TIME_DEVIATION
        self.init_stack_restart_params()

    def init_stack_restart_params(self):
        self.connected_clients_num = 0
        self.stack_restart_is_needed = False
        self.last_start_time = time.time()
        self.next_restart_min_time = self.last_start_time + \
            self.min_stack_restart_timeout + \
            randint(0, self.max_stack_restart_time_deviation)

    def check_listener_events(self):
        events = self.get_monitor_events(self.listener_monitor)
        for event in events:
            logger.trace('{} listener event: {}'.format(self, event))
            if event['event'] == zmq.EVENT_ACCEPTED:
                self.connected_clients_num += 1
            if event['event'] == zmq.EVENT_DISCONNECTED:
                assert self.connected_clients_num > 0
                self.connected_clients_num -= 1
            logger.trace('{}: number of connected clients: {}'.format(self, self.connected_clients_num))

    def restart(self):
        logger.warning("Stopping client stack on node {}".format(self))
        self.stop()
        time.sleep(0.2)
        logger.warning("Starting client stack on node {}".format(self))
        self.start()
        # Sleep to allow disconnected clients to reconnect before sending replies from the server side.
        time.sleep(self.postrestart_wait_time)

    def _can_restart(self):
        return self.next_restart_min_time < time.time()

    def check_for_stack_restart(self):
        stack_restart_is_needed_prev = self.stack_restart_is_needed
        self.stack_restart_is_needed = self.connected_clients_num > self.max_connected_clients_num
        if self.stack_restart_is_needed:
            if not stack_restart_is_needed_prev:
                logger.warning('{}: connections limit exceeded! Actual: {}, limit: {}.'
                               .format(self, self.connected_clients_num, self.max_connected_clients_num))
            if self._can_restart():
                logger.warning('Going to restart client stack {} due to exceeded connections limit! Actual: {}, limit: {}.'
                               .format(self, self.connected_clients_num, self.max_connected_clients_num))
                self.restart()
                self.init_stack_restart_params()
            elif not stack_restart_is_needed_prev:
                logger.warning(
                    '{}: connections limit exceeded but too few time spent since client stack start, restart it later.'
                    .format(self))
        elif stack_restart_is_needed_prev:
            logger.warning('{}: client stack restart is not needed anymore. Connections: Actual: {}, limit: {}.'
                           .format(self, self.connected_clients_num, self.max_connected_clients_num))

    def serviceClientStack(self):
        self.check_listener_events()
        self.check_for_stack_restart()

    def newClientsConnected(self, newClients):
        raise NotImplementedError("{} must implement this method".format(self))

    def transmitToClient(self, msg: Any, remoteName: str):
        """
        Transmit the specified message to the remote client specified by `remoteName`.

        :param msg: a message
        :param remoteName: the name of the remote
        """
        payload = self.prepForSending(msg)
        try:
            if isinstance(remoteName, str):
                remoteName = remoteName.encode()
            self.send(payload, remoteName)
        except Exception as ex:
            # TODO: This should not be an error since the client might not have
            # sent the request to all nodes but only some nodes and other
            # nodes might have got this request through PROPAGATE and thus
            # might not have connection with the client.
            logger.error(
                "{}{} unable to send message {} to client {}; Exception: {}" .format(
                    CONNECTION_PREFIX, self, msg, remoteName, ex.__repr__()))

    def transmitToClients(self, msg: Any, remoteNames: List[str]):
        # TODO: Handle `remoteNames`
        for nm in self.peersWithoutRemotes:
            self.transmitToClient(msg, nm)


class NodeZStack(Batched, KITZStack):
    def __init__(self, stackParams: dict, msgHandler: Callable,
                 registry: Dict[str, HA], seed=None, sighex: str=None,
                 config=None):
        config = config or getConfig()
        Batched.__init__(self, config=config)
        KITZStack.__init__(self, stackParams, msgHandler, registry=registry,
                           seed=seed, sighex=sighex, config=config)
        MessageProcessor.__init__(self, allowDictOnly=False)

    # TODO: Reconsider defaulting `reSetupAuth` to True.
    def start(self, restricted=None, reSetupAuth=True):
        KITZStack.start(self, restricted=restricted, reSetupAuth=reSetupAuth)
        # Calling service lifecycle to allow creation of remotes
        # that this stack needs to connect to
        # self.serviceLifecycle()
        logger.info("{}{} listening for other nodes at {}:{}".
                    format(CONNECTION_PREFIX, self, *self.ha),
                    extra={"tags": ["node-listening"]})


nodeStackClass = NodeZStack
clientStackClass = ClientZStack
