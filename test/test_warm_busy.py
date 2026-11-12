from fs42_live import hub as hub_mod


class FakeChannel:
    def __init__(self, station):
        self.closed = False
        self.warmed_until = 0.0
        self.held = 0

    def hold(self, seconds):
        self.held += 1


def _setup(monkeypatch, busy):
    monkeypatch.setattr(hub_mod, "_Channel", FakeChannel)
    monkeypatch.setattr(hub_mod, "_busy", lambda: busy)
    monkeypatch.setattr(hub_mod.settings, "get", lambda key: 5)
    return hub_mod.ChannelHub()


def test_a_busy_box_starts_no_channel_ahead(monkeypatch):
    h = _setup(monkeypatch, busy=True)
    h.warm({"channel_number": 4})
    assert h._channels == {}


def test_a_busy_box_keeps_one_already_running_warm(monkeypatch):
    h = _setup(monkeypatch, busy=False)
    h.warm({"channel_number": 4})
    monkeypatch.setattr(hub_mod, "_busy", lambda: True)
    h.warm({"channel_number": 4})
    assert h._channels[(4, None)].held == 2


def test_a_quiet_box_starts_it(monkeypatch):
    h = _setup(monkeypatch, busy=False)
    h.warm({"channel_number": 4})
    assert (4, None) in h._channels
