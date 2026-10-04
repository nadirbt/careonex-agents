import array
import math

from nova_sonic.audio import EchoGate, rms_int16


def tone(amplitude: int, n: int = 512) -> bytes:
    return array.array("h", [int(amplitude * math.sin(i / 7.0)) for i in range(n)]).tobytes()


def test_rms_scales_with_amplitude():
    assert rms_int16(b"") == 0.0
    assert 150 < rms_int16(tone(300)) < 300 and rms_int16(tone(9000)) > 5000


def test_everything_passes_when_assistant_is_silent():
    g = EchoGate(min_rms=250, ratio=2.5)
    assert g.should_forward(tone(10), playing=False)
    assert g.floor == 0.0


def test_gate_learns_echo_floor_and_passes_louder_speech():
    g = EchoGate(min_rms=250, ratio=2.5, hold_chunks=3)
    echo = tone(600)  # speaker bleed, RMS ~420
    for _ in range(10):
        assert not g.should_forward(echo, playing=True)
    assert 380 < g.floor < 460
    assert not g.should_forward(tone(900), playing=True)   # a bit louder than echo: still echo-ish
    assert g.should_forward(tone(3000), playing=True)      # real speech: 2.5x floor
    # hold keeps the gate open for the next chunks even if they are quiet (speech onset / gaps)
    assert g.should_forward(echo, playing=True) and g.should_forward(echo, playing=True) and g.should_forward(echo, playing=True)
    assert not g.should_forward(echo, playing=True)       # hold exhausted


def test_quiet_room_uses_min_rms_not_floor():
    g = EchoGate(min_rms=250, ratio=2.5)
    for _ in range(5):
        g.should_forward(tone(20), playing=True)  # near-silent echo
    assert g.threshold == 250
    assert g.should_forward(tone(400), playing=True)


def test_half_duplex_and_disabled():
    assert not EchoGate(half_duplex=True).should_forward(tone(20000), playing=True)
    assert EchoGate(enabled=False, half_duplex=False).should_forward(tone(5), playing=True)
