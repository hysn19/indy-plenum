from plenum.common.channel import RxChannel


# TODO: We settled on naming it 3PCState, however it looks like this is more than just
#  state of 3-phase commit, it includes states of multiple 3-phase commits, checkpoints
#  and view change. What about naming it more generically, like ConsensusState?
class ThreePCState:
    """
    This is a 3PC-state shared between Ordering, Checkpoint and ViewChange services.
    TODO: Consider depending on audit ledger
    TODO: Consider adding persistent local storage for 3PC certificates
    """
    def __init__(self):
        self._view_no = 0
        self._waiting_for_new_view = False

    @property
    def view_no(self) -> int:
        return self._view_no

    @property
    def waiting_for_new_view(self) -> bool:
        return self._waiting_for_new_view

    def enter_next_view(self):
        self._view_no += 1
        self._waiting_for_new_view = True

    def on_update(self) -> RxChannel:
        """
        Channel with important update events

        :return: channel to subscribe
        """
        raise NotImplemented
