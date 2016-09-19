from copy import copy

import pytest
from plenum.common.txn import USER

from plenum.client.signer import SimpleSigner
from plenum.common.looper import Looper
from plenum.common.raet import initLocalKeep
from plenum.common.types import CLIENT_STACK_SUFFIX
from plenum.common.util import getlogger, getMaxFailures, \
    randomString
from plenum.test.eventually import eventually
from plenum.test.helper import TestNode, TestClient, genHa, \
    checkNodesConnected, sendReqsToNodesAndVerifySuffReplies, \
    checkProtocolInstanceSetup
from plenum.test.node_catchup.helper import checkNodeLedgersForEquality, \
    ensureClientConnectedToNodesAndPoolLedgerSame
from plenum.test.pool_transactions.helper import addNewClient, addNewNode, \
    changeNodeIp, addNewStewardAndNode, changeNodeKeys

logger = getlogger()

# logged errors to ignore
whitelist = ['found legacy entry', "doesn't match", "reconciling nodeReg",
             "missing", "conflicts", "matches", "nodeReg", "conflicting address"]


@pytest.yield_fixture(scope="module")
def looper():
    with Looper() as l:
        yield l


@pytest.fixture(scope="module")
def steward1(looper, txnPoolNodeSet, poolTxnStewardData, tdirWithPoolTxns):
    name, sigseed = poolTxnStewardData
    signer = SimpleSigner(seed=sigseed)
    steward = TestClient(name=name, nodeReg=None, ha=genHa(),
                         signer=signer, basedirpath=tdirWithPoolTxns)
    looper.add(steward)
    ensureClientConnectedToNodesAndPoolLedgerSame(looper, steward,
                                                  *txnPoolNodeSet)
    return steward


@pytest.fixture(scope="module")
def client1(txnPoolNodeSet, poolTxnClientData, tdirWithPoolTxns):
    name, sigseed = poolTxnClientData
    signer = SimpleSigner(seed=sigseed)
    client = TestClient(name=name, nodeReg=None, ha=genHa(),
                         signer=signer, basedirpath=tdirWithPoolTxns)
    return client


@pytest.fixture("module")
def nodeThetaAdded(looper, txnPoolNodeSet, tdirWithPoolTxns, tconf, steward1,
                   allPluginsPath):
    newStewardName = "testClientSteward" + randomString(3)
    newNodeName = "Theta"
    newSteward, newNode = addNewStewardAndNode(looper, steward1, newStewardName,
                                               newNodeName,
                                               tdirWithPoolTxns, tconf,
                                               allPluginsPath)
    txnPoolNodeSet.append(newNode)
    looper.run(eventually(checkNodesConnected, txnPoolNodeSet, retryWait=1,
                          timeout=5))
    ensureClientConnectedToNodesAndPoolLedgerSame(looper, steward1,
                                                  *txnPoolNodeSet)
    ensureClientConnectedToNodesAndPoolLedgerSame(looper, newSteward,
                                                  *txnPoolNodeSet)
    return newSteward, newNode


@pytest.fixture("module")
def newHa():
    return genHa(2)


def testNodesConnect(txnPoolNodeSet):
    pass


def testNodesReceiveClientMsgs(looper, wallet1, client1, txnPoolNodeSet):
    looper.add(client1)
    ensureClientConnectedToNodesAndPoolLedgerSame(looper, client1,
                                                  *txnPoolNodeSet)
    sendReqsToNodesAndVerifySuffReplies(looper, wallet1, client1, 1)


def testAddNewClient(looper, txnPoolNodeSet, steward1):
    newSigner = addNewClient(USER, looper, steward1, randomString())

    def chk():
        for node in txnPoolNodeSet:
            assert newSigner.verstr in node.clientAuthNr.clients

    looper.run(eventually(chk, retryWait=1, timeout=5))


def testStewardCannotAddMoreThanOneNode(looper, txnPoolNodeSet, steward1,
                                        tdirWithPoolTxns, tconf,
                                        allPluginsPath):
    newNodeName = "Epsilon"
    with pytest.raises(AssertionError):
        addNewNode(looper, steward1, newNodeName, tdirWithPoolTxns, tconf,
                   allPluginsPath)


def testClientConnectsToNewNode(looper, txnPoolNodeSet, tdirWithPoolTxns,
                                tconf, steward1, allPluginsPath):
    """
    A client should be able to connect to a newly added node
    """
    newStewardName = "testClientSteward"+randomString(3)
    newNodeName = "Epsilon"
    oldNodeReg = copy(steward1.nodeReg)
    newSteward, newNode = addNewStewardAndNode(looper, steward1, newStewardName,
                                               newNodeName,
                                               tdirWithPoolTxns, tconf,
                                               allPluginsPath)
    txnPoolNodeSet.append(newNode)
    looper.run(eventually(checkNodesConnected, txnPoolNodeSet, retryWait=1,
                          timeout=5))
    logger.debug("{} connected to the pool".format(newNode))

    def chkNodeRegRecvd():
        assert (len(steward1.nodeReg) - len(oldNodeReg)) == 1
        assert (newNode.name + CLIENT_STACK_SUFFIX) in steward1.nodeReg

    looper.run(eventually(chkNodeRegRecvd, retryWait=1, timeout=5))
    ensureClientConnectedToNodesAndPoolLedgerSame(looper, steward1,
                                                  *txnPoolNodeSet)
    ensureClientConnectedToNodesAndPoolLedgerSame(looper, newSteward,
                                                  *txnPoolNodeSet)


def testAdd2NewNodes(looper, txnPoolNodeSet, tdirWithPoolTxns, tconf, steward1,
                     allPluginsPath):
    """
    Add 2 new nodes to trigger replica addition and primary election
    """
    for nodeName in ("Zeta", "Eta"):
        newStewardName = "testClientSteward"+randomString(3)
        newSteward, newNode = addNewStewardAndNode(looper, steward1,
                                                   newStewardName,
                                                   nodeName,
                                                   tdirWithPoolTxns, tconf,
                                                   allPluginsPath)
        txnPoolNodeSet.append(newNode)
        looper.run(eventually(checkNodesConnected, txnPoolNodeSet, retryWait=1,
                              timeout=5))
        logger.debug("{} connected to the pool".format(newNode))
        looper.run(eventually(checkNodeLedgersForEquality, newNode,
                              *txnPoolNodeSet[:-1], retryWait=1, timeout=7))

    f = getMaxFailures(len(txnPoolNodeSet))

    def checkFValue():
        for node in txnPoolNodeSet:
            assert node.f == f
            assert len(node.replicas) == (f + 1)

    looper.run(eventually(checkFValue, retryWait=1, timeout=5))
    checkProtocolInstanceSetup(looper, txnPoolNodeSet, retryWait=1,
                               timeout=5)


def testNodePortChanged(looper, txnPoolNodeSet, tdirWithPoolTxns,
                        tconf, steward1, nodeThetaAdded, newHa):
    """
    An running node's port is changed
    """
    newSteward, newNode = nodeThetaAdded
    newNode.stop()
    nodeNewHa, clientNewHa = newHa
    changeNodeIp(looper, newSteward,
                 newNode, nodeHa=nodeNewHa, clientHa=clientNewHa)
    looper.removeProdable(name=newNode.name)
    node = TestNode(newNode.name, basedirpath=tdirWithPoolTxns, config=tconf,
                    ha=nodeNewHa, cliha=clientNewHa)
    looper.add(node)
    # The last element of `txnPoolNodeSet` is the node Theta that was just
    # stopped
    txnPoolNodeSet[-1] = node
    looper.run(eventually(checkNodesConnected, txnPoolNodeSet, retryWait=1,
                          timeout=5))
    looper.run(eventually(checkNodeLedgersForEquality, node,
                          *txnPoolNodeSet[:-1], retryWait=1, timeout=7))
    ensureClientConnectedToNodesAndPoolLedgerSame(looper, steward1,
                                                  *txnPoolNodeSet)
    ensureClientConnectedToNodesAndPoolLedgerSame(looper, newSteward,
                                                  *txnPoolNodeSet)


def testNodeKeysChanged(looper, txnPoolNodeSet, tdirWithPoolTxns,
                        tconf, steward1, nodeThetaAdded, newHa,
                        allPluginsPath=None):
    newSteward, newNode = nodeThetaAdded
    newNode.stop()
    nodeHa, nodeCHa = newHa
    sigseed = randomString(32).encode()
    verkey = SimpleSigner(seed=sigseed).verkey.decode()
    changeNodeKeys(looper, newSteward, newNode, verkey)
    initLocalKeep(newNode.name, tdirWithPoolTxns, sigseed)
    initLocalKeep(newNode.name+CLIENT_STACK_SUFFIX, tdirWithPoolTxns, sigseed)
    looper.removeProdable(name=newNode.name)
    node = TestNode(newNode.name, basedirpath=tdirWithPoolTxns, config=tconf,
                    ha=nodeHa, cliha=nodeCHa, pluginPaths=allPluginsPath)
    looper.add(node)
    # The last element of `txnPoolNodeSet` is the node Theta that was just
    # stopped
    txnPoolNodeSet[-1] = node
    looper.run(eventually(checkNodesConnected, txnPoolNodeSet, retryWait=1,
                          timeout=5))
    looper.run(eventually(checkNodeLedgersForEquality, node,
                          *txnPoolNodeSet[:-1], retryWait=1, timeout=10))
    ensureClientConnectedToNodesAndPoolLedgerSame(looper, steward1,
                                                  *txnPoolNodeSet)
    ensureClientConnectedToNodesAndPoolLedgerSame(looper, newSteward,
                                                  *txnPoolNodeSet)
